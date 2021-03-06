from random import randint
import sys, traceback, threading, socket
import os
from VideoStream import VideoStream
from RtpPacket import RtpPacket

BACKWARD = 0
FORWARD = 1

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	DESCRIBE = 'DESCRIBE'
	FORWARD5SECONDS = 'FORWARD5SECONDS'
	BACKWARD5SECONDS = 'BACKWARD5SECONDS'
	SWITCH = 'SWITCH'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	DEFAULT_CHUNK_SIZE = 1024

	DEFAULT_TIME_CLOCK = 50 # 50ms
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		

	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	

	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	

	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)	
																		  
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])
				
				# Get the RTP/UDP port from the last line
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				self.clientInfo['event'].set()
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			self.state = self.INIT
			print("processing TEARDOWN\n")
			self.clientInfo['event'].set()
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()

		# Process DESCRIBE request
		elif requestType == self.DESCRIBE:
			print ('-'*60 + "\DESCRIBE Request Received\n" + '-'*60)
			msg = self.clientInfo['videoStream'].getVideoInfo()
			reply = 'RTSP/1.0 200 OK\n' + msg
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode('utf-8'))

		# Process FORWARD5SECONDS request
		elif requestType == self.FORWARD5SECONDS:
			print ('-'*60 + "\FORWARD5SECONDS Request Received\n" + '-'*60)
			#TODO: Change frame  + 5 seconds
			self.clientInfo['videoStream'].setFrame(seconds=5, type=FORWARD)
			self.replyRtsp(self.OK_200, seq[1])

		# Process BACKWARD5SECONDS request
		elif requestType == self.BACKWARD5SECONDS:
			print ('-'*60 + "\BACKWARD5SECONDS Request Received\n" + '-'*60)
			#TODO: Change frame  + 5 seconds
			self.clientInfo['videoStream'].setFrame(seconds=5, type=BACKWARD)
			self.replyRtsp(self.OK_200, seq[1])

		# Process SWITCH request
		elif requestType == self.SWITCH:
			print("processing SWITCH\n")
			filenameList = self.queryFilename()
			connSocket = self.clientInfo['rtspSocket'][0]
			self.replyRtsp(self.OK_200, seq[1], filenameList)
		

	def sendRtp(self):
		"""Send RTP packets over UDP."""
		while True:
			self.clientInfo['event'].wait(self.DEFAULT_TIME_CLOCK * 2/1000) 

			# Stop sending if request is PAUSE or TEARDOWN
			# print("Thread is running")				 
			if self.clientInfo['event'].isSet(): 
				break 
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				if frameNumber == 1: # Print first frame data (for debugging purpose)
					# print(data)
					pass
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])
					rtp_packet = self.makeRtp(data, frameNumber)
					print(f"Sending frame {frameNumber}")

					while rtp_packet:
						self.clientInfo['rtpSocket'].sendto(rtp_packet[:self.DEFAULT_CHUNK_SIZE],(address,port))
						rtp_packet = rtp_packet[self.DEFAULT_CHUNK_SIZE:]
				except:
					print("Connection Error")


	def makeRtp(self, payload, frameNbr):
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		marker = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		rtpPacket = RtpPacket()
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		return rtpPacket.getPacket()


	def replyRtsp(self, code, seq, msg=None):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			if msg:
				reply += '\n' + msg
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")


	def queryFilename(self):
		filenameList = ""
		for filename in os.listdir():
			if filename.endswith("Mjpeg"):
				filenameList += filename + ' '
		return filenameList
