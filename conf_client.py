import asyncio
import time
import threading
import socket
import json
import select

from audio_client import *
from config import *
import pyaudio
import cv2
from video_client import capture_and_send, receive_and_display


class ConferenceClient:
    def __init__(self):
        self.is_working = True
        self.server_addr = (SERVER_IP, MAIN_SERVER_PORT)
        self.on_meeting = False
        self.is_manager = False
        self.conns = {'video_socket': None, 'audio_socket': None}
        self.support_data_types = ['text', 'audio', 'video']
        self.share_data = {}
        self.video_running = False
        self.audio_running = False
        self.video_send_port = None
        self.video_recv_port = None
        self.audio_send_port = None
        self.audio_recv_port = None

        self.receive_text_task = None
        self.send_video_task = None
        self.receive_video_task = None
        self.audio_send_task = None
        self.audio_receive_task = None

        self.conference_info = None  # you may need to save and update some conference_info regularly

        self.recv_data = None  # you may need to save received streamed data from other clients in conference

        self.status = 'Free'
        self.username = None
        self.client_socket = None

        self.conf_socket = None
        self.text_socket = None

        self.conference_id = None  # 存储 conference_id

        self.text_port = None

        self.p2p_mode = False  # 标识是否为 P2P 模式
        self.p2p_socket = None  # 用于 P2P 数据传输的 UDP socket
        self.p2p_target = None  # P2P 目标的 (IP, Port)

    def set_username(self):
        self.username = input("Enter your username: ")

    def connect_to_server(self):
        """Establish connection to the server."""
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.connect(self.server_addr)
            print(f"Connected to server at {self.server_addr}")
        except Exception as e:
            print(f"Error connecting to server: {e}")
            self.client_socket = None

    # def create_or_join_conference(self):
    #     """
    #     Request the server for a conference.
    #     If there are available conferences, join one; otherwise, create a new conference.
    #     """
    #     self.on_meeting = True
    #     request = {
    #         "type": "create_conference",  # 向服务器请求获取或创建会议
    #         "data": {}  # 向服务器发送用户名
    #     }
    #     self.send_message(request, self.client_socket)
    #     response =  self.receive_message(self.client_socket)
    #     if response.get('status') == 'success':
    #         self.conference_id = response.get('conference_id')
    #         conf_port = response.get('port')
    #         self.conf_socket.connect((SERVER_IP, conf_port))
    #         print(f"Joined or created conference {self.conference_id}. Server port: {self.conf_socket}")
    #     else:
    #         print("Failed to join or create conference.")

    async def create_conference(self):
        """
        Create a conference: send create-conference request to server and obtain necessary data.
        """
        if self.on_meeting:
            print("In meeting! please leave ongoing meeting first.")
            return

        request = {
            "type": "create_conference",
            "data": {}
        }
        await self.send_message(request, self.client_socket)
        response = await self.receive_message(self.client_socket)
        if response.get('status') == 'success':
            self.on_meeting = True
            self.is_manager = True

            self.conference_id = response.get("conference_id")
            conf_port = response.get('conf_port')
            text_port = response.get("text_port")
            self.video_send_port = response.get("video_send_port")
            self.video_recv_port = response.get("video_recv_port")
            self.audio_send_port = response.get("audio_send_port")
            self.audio_recv_port = response.get("audio_recv_port")

            await self.start_conference(conf_port, text_port, self.audio_send_port, self.audio_recv_port)
            self.status = f'OnMeeting-{self.conference_id}, name: {self.username}'
            print(f"Conference {self.conference_id} created successfully. Server port: {self.conf_socket}.")
        else:
            print("Failed to create conference.")

    async def search_conference(self):
        """
        Search existed conferences.
        """
        request = {
            "type": "search_conference",
            "data": {}
        }
        await self.send_message(request, self.client_socket)
        response = await self.receive_message(self.client_socket)
        print(response)

    async def join_conference(self, conference_id):
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
        await self.send_message(request, self.client_socket)
        response = await self.receive_message(self.client_socket)
        if response.get('status') == 'success':
            self.on_meeting = True
            self.conference_id = response.get("conference_id")
            conf_port = response.get('conf_port')
            text_port = response.get("text_port")
            self.video_send_port = response.get("video_send_port")
            self.video_recv_port = response.get("video_recv_port")
            self.audio_send_port = response.get("audio_send_port")
            self.audio_recv_port = response.get("audio_recv_port")

            await self.start_conference(conf_port, text_port, self.audio_send_port, self.audio_recv_port)
            self.status = f'OnMeeting-{self.conference_id}, name: {self.username}'
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

    async def cancel_conference(self):
        """
        Cancel your ongoing conference (when you are the conference manager).
        """
        if not self.on_meeting:
            print("no ongoing meeting!")
        elif not self.is_manager:
            print("you are not manager, cannot cancel meeting")
        else:
            if self.conf_socket and self.conference_id:
                # request = {
                #     "type": "cancel_conference",
                #     "data": {"conference_id": self.conference_id}
                # }
                # await self.send_message(request, self.client_socket)
                self.conf_socket.send("cancel".encode('utf-8'))
                print(f"Cancelling conference {self.conference_id}")
                # self.on_meeting = False
                # self.is_manager = False
                # self.conference_id = None

    async def send_message(self, request, socket):
        """Send a request message to the server."""
        if socket:
            try:
                request_json = json.dumps(request)
                socket.send(request_json.encode('utf-8'))
                print(f"Sent: {request_json}")
            except Exception as e:
                print(f"Error sending message: {e}")

    async def receive_message(self, socket):
        """Receive a response message from the server."""
        if socket:
            try:
                response = socket.recv(1024)
                return json.loads(response.decode('utf-8'))
            except BlockingIOError:  # This is expected for non-blocking sockets when no data is ready
                await asyncio.sleep(0.1)  # Yield control to other tasks
                return None
            except Exception as e:
                print(f"Error receiving normal message: {e}")
                return None

    async def keep_share(self, data_type, send_conn, capture_function, compress=None, fps_or_frequency=30):
        """
        Running task: keep sharing (capture and send) certain type of data from server or clients (P2P).
        """
        if data_type == 'video':
            await self.capture_video(send_conn)  # 注意这里使用 await
        elif data_type == 'audio':
            await self.capture_audio(send_conn)

    async def share_switch(self, data_type):
        """
        Switch for sharing certain type of data (screen, camera, audio, etc.)
        """
        print(f"video conns {self.conns.get('video_socket')}")
        if data_type == 'video':
            if self.video_running:
                await self.stop_video()
            else:
                await self.start_video()
        elif data_type == 'audio':
            if self.audio_running:
                await self.stop_audio()
            else:
                await self.start_audio()

    async def capture_audio(self, send_conn):
        """
        Capture audio stream and send it to server or other clients.
        """
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

    def play_audio(self, data):
        """Play audio received from the server."""
        # 初始化PyAudio
        p = pyaudio.PyAudio()
        # 打开一个音频流
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=44100, output=True)

        # 播放音频数据
        stream.write(data)

        # 音频播放完毕后，关闭流
        stream.stop_stream()
        stream.close()

        # 关闭PyAudio
        p.terminate()

    async def receive_conf_message(self):
        while True:
            if self.on_meeting:
                try:
                    loop = asyncio.get_running_loop()
                    message = await loop.sock_recv(self.conf_socket, 1024)
                    if message:
                        decoded_message = message.decode('utf-8')
                        if decoded_message == 'cancel':
                            self.close_conference()
                            print('Conference has been canceled. Quit.')
                        else:
                            response = json.loads(message.decode('utf-8'))
                            if response.get('type') == 'p2p':
                                peer_ip = response.get("peer_ip")
                                self.text_port = response.get("text_port")
                                self.video_send_port = response.get("video_send_port")
                                self.audio_send_port = response.get("audio_send_port")

                                if self.video_running:
                                    loop = asyncio.get_running_loop()
                                    if self.send_video_task is not None:
                                        self.send_video_task.cancel()
                                    # if self.receive_video_task is not None:
                                    #     self.receive_video_task.cancel()
                                    self.send_video_task = asyncio.create_task(
                                        capture_and_send(loop, peer_ip, self.video_send_port))
                                    # self.receive_video_task = asyncio.create_task(
                                    #     receive_and_display(loop, self.video_recv_port))

                except Exception as e:
                    print(f"Error receiving conf message: {e}")
                    break
            else:
                break

    async def receive_text_message(self):
        while True:
            if self.on_meeting:
                try:
                    message = await self.receive_message(self.text_socket)
                    if message is not None:
                        text = message.get('text')
                        print(text)
                except Exception as e:
                    print(f"Error receiving text message: {e}")
                    break
            else:
                break

    async def start_conference(self, conf_port, text_port, audio_send_port, audio_recv_port):
        '''
        Init conns when create or join a conference with necessary conference_info.
        '''
        await asyncio.sleep(0.3)
        self.conf_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.conf_socket.connect((SERVER_IP, conf_port))
        self.conf_socket.setblocking(False)
        # connect to text port
        self.text_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.text_socket.connect((SERVER_IP, text_port))
        self.text_socket.setblocking(False)
        # connect to video port

        # connect to audio port
        # self.conns['audio_socket'].connect((SERVER_IP, audio_port))
        self.set_username()

        # asyncio.create_task(self.receive_text_message())
        # asyncio.create_task(self.receive_conf_message())

        task1 = asyncio.create_task(self.receive_conf_message())
        self.receive_text_task = asyncio.create_task(self.receive_text_message())

        # asyncio.gather(task1, self.receive_text_task)

    def close_conference(self):
        '''
        Close all conns to servers or other clients and cancel the running tasks.
        '''
        print(f"Closing all connections for conference {self.conference_id}")
        self.receive_text_task.cancel()

        if self.send_video_task is not None:
            self.send_video_task.cancel()
        if self.receive_video_task is not None:
            self.receive_video_task.cancel()
        self.video_running = False
        if self.audio_send_task is not None:
            self.audio_send_task.cancel()
        if self.audio_receive_task is not None:
            self.audio_receive_task.cancel()

        self.conf_socket.close()
        self.text_socket.close()
        if self.conns['video_socket'] is not None:
            self.conns['video_socket'].close()
        if self.conns['audio_socket'] is not None:
            self.conns['audio_socket'].close()

        self.on_meeting = False
        self.is_manager = False
        self.conference_id = None
        self.status = 'Free'

        # self.conns.clear()
        # if self.client_socket:
        #     self.client_socket.close()

    def read_console_input(self):
        return input(f'({self.status}) Please enter an operation (enter "?" to help): ').strip().lower()
    
    async def start_video(self):
        """
        启动视频功能：发送和接收任务。
        """
        if self.video_running:
            print("Video is already running.")
            return

        self.video_running = True
        loop = asyncio.get_running_loop()
        self.send_video_task = asyncio.create_task(capture_and_send(loop, SERVER_IP, self.video_send_port))
        self.receive_video_task = asyncio.create_task(receive_and_display(loop, self.video_recv_port))
        print("Video started.")

    async def stop_video(self):
        """
        停止视频功能。
        """
        if not self.video_running:
            print("Video is not running.")
            return

        if self.send_video_task is not None:
            self.send_video_task.cancel()
        if self.receive_video_task is not None:
            self.receive_video_task.cancel()

        self.video_running = False

        # 停止发送任务
        if hasattr(self, 'send_task') and self.send_video_task:
            self.send_video_task.cancel()
            try:
                await self.send_video_task
            except asyncio.CancelledError:
                print("Send task cancelled.")

        # 停止接收任务
        if hasattr(self, 'receive_task') and self.receive_video_task:
            self.receive_video_task.cancel()
            try:
                await self.receive_video_task
            except asyncio.CancelledError:
                print("Receive task cancelled.")

        print("Video stopped.")

    async def start_audio(self):
        """
        启动音频功能：发送和接收任务。
        """
        if self.audio_running:
            print("Audio is already running.")
            return

        self.audio_running = True
        loop = asyncio.get_running_loop()
        self.audio_send_task = asyncio.create_task(capture_and_send_audio(loop, SERVER_IP, self.audio_send_port))
        self.audio_receive_task = asyncio.create_task(receive_and_play_audio(loop, self.audio_recv_port))
        print("Audio started.")

    async def stop_audio(self):
        """
        停止音频功能。
        """
        if not self.audio_running:
            print("Audio is not running.")
            return

        self.audio_running = False

        # 停止发送任务
        if hasattr(self, 'audio_send_task') and self.audio_send_task:
            self.audio_send_task.cancel()
            try:
                await self.audio_send_task
            except asyncio.CancelledError:
                print("Audio send task cancelled.")

        print("Audio stopped.")

    async def establish_p2p(self, target_id):
        """
        Establish a P2P connection with the specified target client.
        """
        request = {
            "type": "p2p_request",
            "data": {"target_id": target_id}
        }
        await self.send_message(request, self.client_socket)
        response = await self.receive_message(self.client_socket)

        if response.get("status") == "success":
            target_ip = response["target_ip"]
            target_port = response["target_port"]
            print(f"Establishing P2P connection with {target_ip}:{target_port}")

            # 创建 P2P 连接
            self.p2p_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.p2p_target = (target_ip, target_port)
            self.p2p_mode = True  # 启用 P2P 模式
            return True
        else:
            print("Failed to establish P2P connection:", response.get("message"))
            return False

    async def p2p_send(self, data):
        """
        Send data to the P2P target.
        """
        if self.p2p_socket and self.p2p_target:
            self.p2p_socket.sendto(data, self.p2p_target)

    def exit_p2p_mode(self):
        """
        Exit P2P mode and clean up resources.
        """
        if self.p2p_socket:
            self.p2p_socket.close()
            self.p2p_socket = None
        self.p2p_mode = False


    async def start(self):
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
            # if not self.on_meeting:
            #     status = 'Free'
            # else:
            #     status = f'OnMeeting-{self.conference_id}'

            recognized = True
            cmd_input = await asyncio.to_thread(self.read_console_input)
            # cmd_input = input(f'({self.status}) Please enter an operation (enter "?" to help): ').strip().lower()
            fields = cmd_input.split(maxsplit=1)

            if len(fields) == 1:
                if cmd_input in ('?', '？'):
                    print(HELP)
                elif cmd_input == 'create':
                    await self.create_conference()
                elif cmd_input == 'quit':
                    self.quit_conference()
                elif cmd_input == 'cancel':
                    await self.cancel_conference()
                elif cmd_input == 'search':
                    await self.search_conference()
                else:
                    recognized = False
            elif len(fields) == 2:
                if fields[0] == 'join':
                    input_conf_id = fields[1]
                    await self.join_conference(input_conf_id)
                    # if input_conf_id.isdigit():
                    #     await self.join_conference(input_conf_id)
                    # else:
                    #     print('[Warn]: Input conference ID must be in digital form')
                elif fields[0] == 'switch':
                    data_type = fields[1]
                    if data_type in self.support_data_types:
                        await self.share_switch(data_type)
                elif fields[0] == 'msg:':
                    if self.on_meeting:
                        message = {"text": f'{self.username}: {fields[1]}'}
                        await self.send_message(message, self.text_socket)
                    else:
                        print("Not in any meeting")

                elif fields[0] == 'p2p':  # 匹配 "p2p <target_id>" 命令
                    target_id = fields[1]
                    await self.establish_p2p(target_id)  # 建立 P2P 连接
                else:
                    recognized = False

            if not recognized:
                print(f'[Warn]: Unrecognized cmd_input {cmd_input}')


# SERVER_IP = '10.27.89.235'
# MAIN_SERVER_PORT = 8888
if __name__ == '__main__':
    client1 = ConferenceClient()
    asyncio.run(client1.start())
