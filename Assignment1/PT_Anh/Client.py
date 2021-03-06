from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	RESET = 4

	# Initiation..
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.frameNbr = 0
		self.lossCounter = 0
		self.rtsp_version = "RTSP/1.0"

		self.connectToServer()
		self.VideoThread = None
		self.playEnd = threading.Event()
		self.notPausing = threading.Event()
		self.finishReset = threading.Event()

	def createWidgets(self):
		"""Build GUI."""
		# Create Setup button
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=1, column=0, padx=2, pady=2)

		# Create Play button
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=1, column=1, padx=2, pady=2)

		# Create Pause button
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=1, column=2, padx=2, pady=2)

		# Create Teardown button
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=1, column=3, padx=2, pady=2)

		# Create a label to display the movie
		self.label = Label(self.master, height=19)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
		else:
			if self.state == self.PLAYING:
				thread = threading.Thread(target=self.pauseMovie)
				thread.start()
				thread.join()
			while True:
				if self.notPausing.isSet() == False:
					self.finishReset.clear()
					thread = threading.Thread(target=self.resetMovie)
					thread.start()
					thread.join()
					while True:
						if self.finishReset.isSet():
							self.playMovie()
							break
					break
			

	def exitClient(self):
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)
		self.master.destroy() # Close the gui window
		os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT) # Delete the cache image from video
		sys.exit(0)

	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)

	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			# Create a new thread to listen for RTP packets
			self.playEnd.clear()
			self.sendRtspRequest(self.PLAY)
			self.VideoThread = threading.Thread(target=self.listenRtp)
			self.VideoThread.start()
	
	def resetMovie(self):
		if self.state == self.READY:
			self.sendRtspRequest(self.RESET)
			
	def listenRtp(self):
		while True:
			try:
				# Receive Data 
				data,addr = self.rtpSocket.recvfrom(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					print ("//Received Rtp Packet #" + str(rtpPacket.seqNum()) + "//")
					try:
						if self.frameNbr >= rtpPacket.seqNum():
							self.lossCounter += self.frameNbr - rtpPacket.seqNum()
							print ("PACKET LOSS\n")
						currFrameNbr = rtpPacket.seqNum()
					except:
						print ("seqNum() error")
					if currFrameNbr > self.frameNbr: # Discard the previous packet arrive late
						self.frameNbr = currFrameNbr
						self.updateMovie(self.writeFrame(rtpPacket.getPayload()))

			except:
				# Not Receive Data 
				print ("Data Missing!")
				# Stop listening upon requesting PAUSE or TEARDOWN
				if self.playEnd.isSet():
					self.notPausing.clear()
					break

				# Upon receiving ACK for TEARDOWN request, close the RTP socket
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
					break

	def writeFrame(self, data):
		"""Write the received frame to a temp image file. Return the image file."""
		cacheimage = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		try:
			file = open(cacheimage, "wb")
			file.write(data)
		except:
			print ("Error while trying to writeFrame")
		file.close()
		return cacheimage

	def updateMovie(self, imageFile):
		"""Update the image file as video frame in the GUI."""
		try:
			photo = ImageTk.PhotoImage(Image.open(imageFile))
		except:
			print ("Image not Found")

		self.label.configure(image = photo, height=288)
		self.label.image = photo

	def connectToServer(self):
		"""Connect to the Server. Start a new RTSP/TCP session."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.rtspSocket.connect((self.serverAddr, self.serverPort))

	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""
		# Setup request
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start() # Establish RTSP Connection
			self.rtspSeq = 1
			request_line = [ 
				f"SETUP {self.fileName} {self.rtsp_version}", 
				f"CSeq: {self.rtspSeq}", 
				f"Transport: RTP/UDP; client_port= {self.rtpPort}"
			]            
			request = '\n'.join(request_line) + '\n'
			self.rtspSocket.send(request.encode('utf-8'))
			
			# Keep track of the sent request.
			self.requestSent = self.SETUP

		# Play request
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq = self.rtspSeq + 1
			# Write the RTSP request to be sent.
			request_line = [
				f"PLAY {self.fileName} {self.rtsp_version}", 
				f"CSeq: {self.rtspSeq}", 
				f"Session: {self.sessionId}"
			]
			request = '\n'.join(request_line) + '\n'
			self.rtspSocket.send(request.encode('utf-8'))
			# Keep track of the sent request.
			self.requestSent = self.PLAY

		# Pause request
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			# Update RTSP sequence number.
			self.rtspSeq = self.rtspSeq + 1
			request_line = [
				f"PAUSE {self.fileName} {self.rtsp_version}", 
				f"CSeq: {self.rtspSeq}", 
				f"Session: {self.sessionId}"
			]
			request = '\n'.join(request_line) + '\n'
			self.rtspSocket.send(request.encode('utf-8'))
			# Keep track of the sent request.
			self.requestSent = self.PAUSE

		# Reset request
		elif requestCode == self.RESET and self.state == self.READY:
			# Update RTSP sequence number.
			self.frameNbr = 0
			self.rtspSeq = self.rtspSeq + 1
			request_line = [
				f"RESET {self.fileName} {self.rtsp_version}", 
				f"CSeq: {self.rtspSeq}", 
				f"Session: {self.sessionId}"
			]
			request = '\n'.join(request_line) + '\n'
			self.rtspSocket.send(request.encode('utf-8'))
			# Keep track of the sent request.
			self.requestSent = self.RESET

		# Teardown request
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			# Update RTSP sequence number.
			request_line = [
				f"TEARDOWN {self.fileName} {self.rtsp_version}", 
				f"CSeq: {self.rtspSeq}", 
				f"Session: {self.sessionId}"
			]
			request = '\n'.join(request_line) + '\n'
			self.rtspSocket.send(request.encode('utf-8'))
			# Keep track of the sent request.
			self.requestSent = self.TEARDOWN

		else:
			return

	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			if reply:
				self.parseRtspReply(reply)

			# Close the RTSP socket upon requesting Teardown
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break

	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.decode('utf-8').split('\n')
		seqNum = int(lines[1].split(' ')[1])

		# Process only if the server reply's sequence number is the same as the request's
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			# New RTSP session ID
			if self.sessionId == 0:
				self.sessionId = session

			# Process only if the session ID is the same
			if self.sessionId == session:
				if int(lines[0].split(' ')[1]) == 200:
					if self.requestSent == self.SETUP:
						# Update RTSP state.
						self.state = self.READY
						# Open RTP port.
						self.openRtpPort()

					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
						self.notPausing.set()
					
					elif self.requestSent == self.RESET:
						self.finishReset.set()

					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						self.playEnd.set()

					elif self.requestSent == self.TEARDOWN:
						# Flag the teardownAcked to close the socket.
						self.teardownAcked = 1

	def openRtpPort(self):
		"""Open RTP socket binded to a specified port."""
		# Create a new datagram socket to receive RTP packets from the server
		self.rtpSocket = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
		# Set the timeout value of the socket to 0.5sec
		self.rtpSocket.settimeout(0.5)

		try:
			self.rtpSocket.bind((self.serverAddr,self.rtpPort)) 
			print ("Bind RtpPort Success")

		except:
			tkinter.messagebox.showwarning('Connection Failed', 'Connection to rtpServer failed...')


	def handler(self):
		"""Handler on explicitly closing the GUI window."""
		self.pauseMovie()
		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: 
			threading.Thread(target=self.listenRtp).start()
			self.sendRtspRequest(self.PLAY)
