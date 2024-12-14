import asyncio
import time
import threading
import socket
import json
import pyaudio
import cv2
from util import *


class ConferenceClient:
    def __init__(self):
        self.is_working = True
        self.server_addr = (SERVER_IP, MAIN_SERVER_PORT)
        self.on_meeting = False
        self.conns = {'video_socket': None, 'audio_socket': None}
        self.support_data_types = ['text', 'audio', 'video']
        self.share_data = {}

        self.conference_info = None  # you may need to save and update some conference_info regularly

        self.recv_data = None  # you may need to save received streamed data from other clients in conference

        self.username = None
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.conf_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conns['video_socket'] = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conns['audio_socket'] = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.text_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.conference_id = None  # 存储 conference_id

    def set_username(self):
        self.username = input("Enter your username: ")

    def connect_to_server(self):
        """Establish connection to the server."""
        try:
            self.client_socket.connect(self.server_addr)
            print(f"Connected to server at {self.server_addr}")
        except Exception as e:
            print(f"Error connecting to server: {e}")
            self.client_socket = None

    def create_or_join_conference(self):
        """
        Request the server for a conference.
        If there are available conferences, join one; otherwise, create a new conference.
        """
        self.on_meeting = True
        request = {
            "type": "create_conference",  # 向服务器请求获取或创建会议
            "data": {}  # 向服务器发送用户名
        }
        self.send_message(request)
        response = self.receive_message()
        if response.get('status') == 'success':
            self.conference_id = response.get('conference_id')
            conf_port = response.get('port')
            self.conf_socket.connect((SERVER_IP, conf_port))
            print(f"Joined or created conference {self.conference_id}. Server port: {self.conf_socket}")
        else:
            print("Failed to join or create conference.")

    def create_conference(self):
        """
        Create a conference: send create-conference request to server and obtain necessary data.
        """
        request = {
            "type": "create_conference",
            "data": {}
        }
        self.send_message(request)
        response = self.receive_message()
        if response.get('status') == 'success':
            self.on_meeting = True
            self.conference_id = response.get("conference_id")
            conf_port = response.get('conf_port')
            text_port = response.get("text_port")
            video_port = response.get("video_port")
            audio_port = response.get("audio port")

            self.start_conference(conf_port, text_port, video_port, audio_port)
            print(f"Conference {self.conference_id} created successfully. Server port: {self.conf_socket}.")
        else:
            print("Failed to create conference.")

    def join_conference(self, conference_id):
        """
        Join a conference: send join-conference request with given conference_id.
        """
        if self.on_meeting:
            print("In meeting! please leave ongoing meeting first.")
            return

        self.conference_id = conference_id
        request = {
            "type": "join_conference",
            "data": {
                "conference_id": self.conference_id
            }
        }
        self.send_message(request)
        response = self.receive_message()
        if response.get('status') == 'success':
            self.on_meeting = True
            self.conf_socket = response.get('port')
            print(f"Joined conference {self.conference_id}. Server port: {self.conf_socket}")
        else:
            print(f"Failed to join conference {self.conference_id}.")

    def quit_conference(self):
        """
        Quit your ongoing conference.
        """
        if not self.on_meeting:
            print("No ongoing meeting!")
            return
        self.close_conference()
        self.on_meeting = False
        self.conference_id = None
        print("Quit successfully")

        # if self.client_socket and self.conference_id:
        #     request = {
        #         "type": "quit_conference",
        #         "data": {
        #             "conference_id": self.conference_id
        #         }
        #     }
        #     self.send_message(request)
        #     print(f"Exiting conference {self.conference_id}")
        #     self.on_meeting = False
        #     self.conference_id = None

    def cancel_conference(self):
        """
        Cancel your ongoing conference (when you are the conference manager).
        """
        if self.client_socket and self.conference_id:
            request = {
                "type": "cancel_conference",
                "data": {"conference_id": self.conference_id}
            }
            self.send_message(request)
            print(f"Cancelling conference {self.conference_id}")
            self.on_meeting = False
            self.conference_id = None

    def send_message(self, request):
        """Send a request message to the server."""
        if self.client_socket:
            try:
                request_json = json.dumps(request)
                self.client_socket.send(request_json.encode('utf-8'))
                print(f"Sent: {request_json}")
            except Exception as e:
                print(f"Error sending message: {e}")

    def receive_message(self):
        """Receive a response message from the server."""
        if self.client_socket:
            try:
                response = self.client_socket.recv(1024)
                return json.loads(response.decode('utf-8'))
            except Exception as e:
                print(f"Error receiving message: {e}")
                return None

    def keep_share(self, data_type, send_conn, capture_function, compress=None, fps_or_frequency=30):
        '''
        Running task: keep sharing (capture and send) certain type of data from server or clients (P2P).
        '''
        if data_type == 'video':
            self.capture_video(send_conn)
        elif data_type == 'audio':
            self.capture_audio(send_conn)

    def share_switch(self, data_type):
        '''
        Switch for sharing certain type of data (screen, camera, audio, etc.)
        '''
        if data_type == 'video':
            self.keep_share('video', self.conns.get('video'), capture_function=self.capture_video)
        elif data_type == 'audio':
            self.keep_share('audio', self.conns.get('audio'), capture_function=self.capture_audio)

    def capture_video(self, send_conn):
        '''
        Capture video stream from camera and send it to server or other clients.
        '''
        cap = cv2.VideoCapture(0)
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            # Compress if needed
            send_conn.send(frame)  # Send the frame to server/other clients

    def capture_audio(self, send_conn):
        '''
        Capture audio stream and send it to server or other clients.
        '''
        audio = pyaudio.PyAudio()
        stream = audio.open(format=pyaudio.paInt16, channels=1, rate=44100, input=True, frames_per_buffer=1024)
        while True:
            data = stream.read(1024)
            send_conn.send(data)  # Send the audio data to server/other clients

    def keep_recv(self, recv_conn, data_type, decompress=None):
        '''
        Running task: keep receiving certain type of data (save or output).
        '''
        while True:
            data = recv_conn.recv(1024)
            if data_type == 'video':
                self.show_video(data)
            elif data_type == 'audio':
                self.play_audio(data)

    def show_video(self, data):
        """Display the video stream received from the server."""
        # Assuming 'data' is a frame from the video stream
        cv2.imshow('Video', data)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            cv2.destroyAllWindows()

    def play_audio(self, data):
        """Play audio received from the server."""
        # Play audio using pyaudio or any other audio library
        pass

    def start_conference(self, conf_port, text_port, video_port, audio_port):
        '''
        Init conns when create or join a conference with necessary conference_info.
        '''
        self.conf_socket.connect(conf_port)
        # connect to text port
        self.text_socket.connect((SERVER_IP, text_port))
        # connect to video port
        self.conns['video_socket'].connect((SERVER_IP, video_port))
        # connect to audio port
        self.conns['audio_socket'].connect((SERVER_IP, audio_port))
        self.set_username()

    def close_conference(self):
        '''
        Close all conns to servers or other clients and cancel the running tasks.
        '''
        print(f"Closing all connections for conference {self.conference_id}")
        # self.conf_socket.close()
        self.text_socket.close()
        self.conns['video_socket'].close()
        self.conns['audio_socket'].close()

        # self.conns.clear()
        # if self.client_socket:
        #     self.client_socket.close()

    def start(self):
        """
        Execute functions based on the command line input.
        """
        self.connect_to_server()
        if not self.client_socket:
            print("Unable to connect to server, exiting.")
            return

        # # After connecting, automatically request to join or create a conference
        # self.create_or_join_conference()

        while True:
            if not self.on_meeting:
                status = 'Free'
            else:
                status = f'OnMeeting-{self.conference_id}'

            recognized = True
            cmd_input = input(f'({status}) Please enter a operation (enter "?" to help): ').strip().lower()
            fields = cmd_input.split(maxsplit=1)
            if len(fields) == 1:
                if cmd_input in ('?', '？'):
                    print(HELP)
                elif cmd_input == 'create':
                    self.create_conference()
                elif cmd_input == 'quit':
                    self.quit_conference()
                elif cmd_input == 'cancel':
                    self.cancel_conference()
                else:
                    recognized = False
            elif len(fields) == 2:
                if fields[0] == 'join':
                    input_conf_id = fields[1]
                    if input_conf_id.isdigit():
                        self.join_conference(input_conf_id)
                    else:
                        print('[Warn]: Input conference ID must be in digital form')
                elif fields[0] == 'switch':
                    data_type = fields[1]
                    if data_type in self.support_data_types:
                        self.share_switch(data_type)
                else:
                    recognized = False

            if not recognized:
                print(f'[Warn]: Unrecognized cmd_input {cmd_input}')


if __name__ == '__main__':
    client1 = ConferenceClient()
    client1.start()
