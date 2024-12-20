import asyncio
# from util import *
import json
import socket
import time


def get_free_port():
    """
    获取一个空闲的端口
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # 绑定到地址 ('', 0)，让操作系统分配一个空闲端口
        s.bind(('', 0))
        # 获取分配的端口号
        port = s.getsockname()[1]
        return port


class ConferenceServer:
    def __init__(self, main_server):
        # the main_server it belongs to
        self.main_server = main_server

        self.conf_server = None
        self.running = False

        # async server
        self.conference_id = None  # conference_id for distinguish difference conference
        self.conf_serve_port = None
        self.data_serve_ports = {}

        self.conf_client_readers = set()
        self.conf_client_writers = set()

        self.text_readers = set()
        self.text_writers = set()
        
        self.video_readers = set()
        self.video_writers = set()

        self.data_types = ['screen', 'camera', 'audio']  # example data types in a video conference
        self.clients_info = None
        # self.client_conns = None
        self.mode = 'Client-Server'  # or 'P2P' if you want to support peer-to-peer conference mode

        self.text_serve_port = None
        self.video_serve_port = None
        self.audio_serve_port = None

    # async def handle_data(self, reader, writer, data_type):
    #     """
    #     running task: receive sharing stream data from a client and decide how to forward them to the rest clients
    #     从客户端接收数据并将其转发给会议中的其他与会者
    #     """
    #     try:
    #         while True:
    #             data = await reader.read(1024)  # Read data from client 从数据流中读取
    #             if not data:
    #                 break  # Client disconnected
    #             # Forward the data to other clients
    #             for client_writer in self.client_conns:  #遍历连接会议的所有客户端
    #                 if client_writer is not writer:  #排除将数据传回给自己的情况
    #                     client_writer.write(data)
    #                     await client_writer.drain()
    #     except Exception as e:
    #         print(f"Error handling data of type {data_type}: {e}")
    #     finally:
    #         writer.close()
    #         await writer.wait_closed()

    async def handle_conf_client(self, reader, writer):
        """
        running task: handle the in-meeting requests or messages from clients
        """
        self.conf_client_readers.add(reader)
        self.conf_client_writers.add(writer)
        try:
            # 获取客户端的地址信息（用于调试或日志）
            client_address = writer.get_extra_info('peername')
            print(f"Connected to client {client_address}")

            while True:
                # 接收客户端的请求
                data = await reader.read(1024)
                if not data:
                    print(f"Client {client_address} disconnected.")
                    break  # 客户端断开连接

                # 解码收到的数据
                message = data.decode()
                if message == 'cancel':
                    await self.cancel_conference()
                # print(f"Received from {client_address}: {message}")
                #
                # # 解析请求数据（假设是 JSON 格式）需要统一请求数据的格式
                # import json
                # try:
                #     request = json.loads(message)
                #     request_type = request.get("type")  # 请求类型
                #     payload = request.get("data")  # 请求的具体数据
                # except json.JSONDecodeError:
                #     print("Invalid data format received.")
                #     continue
                #
                # # 根据请求类型执行相应的操作
                # if request_type == "send_message":
                #     # 转发文本消息
                #     await self.broadcast_message(writer, payload, "text")
                # elif request_type == "send_video":
                #     # 开启视频流的处理
                #     print(f"Starting video stream for {client_address}.")
                #     await self.handle_data(reader, writer, "video")
                # elif request_type == "send_audio":
                #     # 开启音频流的处理
                #     print(f"Starting audio stream for {client_address}.")
                #     await self.handle_data(reader, writer, "audio")
                # elif request_type == "exit":
                #     # 客户端退出会议
                #     print(f"Client {client_address} has exited the meeting.")
                #     break
                # else:
                #     print(f"Unknown request type: {request_type}")
        except Exception as e:
            print(f"Error handling client {client_address}: {e}")
        finally:
            self.conf_client_readers.remove(reader)
            self.conf_client_writers.remove(writer)
            # 清理资源
            writer.close()
            # await writer.wait_closed()
            if len(self.conf_client_readers) == 0:
                if self.conference_id in self.main_server.conference_servers:
                    del self.main_server.conference_servers[self.conference_id]
                self.running = False
                self.conf_server.close()
                print("Server is shutting down...")
            print(f"Connection to client {client_address} closed.")

    async def handle_text_client(self, reader, writer):
        self.text_readers.add(reader)
        self.text_writers.add(writer)
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                await self.broadcast_data(data, writer, 'text')
        except Exception as e:
            print(f"Text socket error: {e}")
        finally:
            self.text_readers.remove(reader)
            self.text_writers.remove(writer)
            writer.close()
            # await writer.wait_closed()
            
    async def handle_video_client(self, reader, writer):
        self.video_readers.add(reader)
        self.video_writers.add(writer)
        try:
            while True:
                # 读取视频数据，假设每次读取 4096 字节的流数据（根据视频流的实际大小调整）
                data = await reader.read(4096)
                if not data:
                    break

                # 将接收到的视频数据广播给其他客户端
                await self.broadcast_video_data(data, writer)
        except Exception as e:
            print(f"Video socket error: {e}")
        finally:
            # 清理资源，移除该连接的 reader 和 writer
            print("clean handle video")
            self.video_readers.remove(reader)
            self.video_writers.remove(writer)
            writer.close()
            await writer.wait_closed()


    async def broadcast_data(self, data, sender, data_type):
        if data_type == 'text':
            for writer in self.text_writers:
                if writer is not sender:
                    writer.write(data)
                    await writer.drain()
                    

    async def log(self):
        """
        Periodically log the server status, including active clients and meetings.
        """
        while self.running:  # self.running 用于控制日志记录的开关
            try:
                print("=== Server Status ===")
                # 打印活跃的会议数量
                active_conferences = len(self.conference_servers)
                print(f"Active Conferences: {active_conferences}")

                # 打印每个会议的详细状态
                for conference_id, conference in self.conference_servers.items():
                    client_count = len(conference.client_conns) if conference.client_conns else 0
                    print(f" - Conference ID: {conference_id}, Clients: {client_count}")

                # 打印其他服务器运行信息（如模式等）
                print(f"Server Mode: {self.mode}")
                print("=====================")

                # 等待指定的时间间隔
                await asyncio.sleep(LOG_INTERVAL)
            except Exception as e:
                print(f"Error during logging: {e}")

    async def cancel_conference(self):
        """
        Handle cancel conference request: disconnect all connections and clean up resources.
        """
        try:
            print(f"Canceling conference {self.conference_id}...")

            cancellation_message = "cancel".encode()
            # 通知所有客户端会议被取消
            to_remove = [item for item in self.conf_client_writers]
            for client_writer in to_remove:
                try:
                    client_writer.write(cancellation_message)
                    await client_writer.drain()
                    client_writer.close()
                    await client_writer.wait_closed()
                    print(f"Disconnected client from conference {self.conference_id}.")
                except Exception as e:
                    print(f"Failed to disconnect a client: {e}")

            # 从服务器的会议列表中移除该会议
            del self.main_server.conference_servers[self.conference_id]

            if self.conf_server is not None:
                self.running = False
                self.conf_server.close()
                print("Server is shutting down...")

            print(f"Conference {self.conference_id} successfully canceled.")
        except Exception as e:
            print(f"Error while canceling conference {self.conference_id}: {e}")

    async def start(self):
        try:
            print(f"Starting ConferenceServer for conference ID: {self.conference_id} on port {self.conf_serve_port}")
            self.running = True
            # self.server = await asyncio.start_server(self.handle_conf_client, SERVER_IP, self.conf_serve_port)

            conf_server = await asyncio.start_server(self.handle_conf_client, SERVER_IP, self.conf_serve_port)
            self.conf_server = conf_server
            text_server = await asyncio.start_server(self.handle_text_client, SERVER_IP, self.text_serve_port)
            video_server = await asyncio.start_server(self.handle_video_client, SERVER_IP, self.video_serve_port)

            server_tasks = [
                asyncio.create_task(conf_server.serve_forever()),
                asyncio.create_task(text_server.serve_forever()),
                asyncio.create_task(video_server.serve_forever())
            ]

            await asyncio.gather(*server_tasks)

            print(f"ConferenceServer is now listening on port {self.conf_serve_port}")

            # async with self.server:
            #     await self.server.serve_forever()
        except Exception as e:
            print(f"Error starting ConferenceServer for conference ID {self.conference_id}: {e}")
        finally:
            self.running = False


class MainServer:
    def __init__(self, server_ip, main_port):
        # async server
        self.server_ip = server_ip
        self.server_port = main_port
        self.main_server = None

        self.conference_conns = None
        self.conference_servers = {}  # self.conference_servers[conference_id] = ConferenceManager

    async def handle_create_conference(self, client_address):
        try:
            conference_id = f"conf_{len(self.conference_servers) + 1}"
            print(f"Creating conference with ID: {conference_id}")

            new_conference = ConferenceServer(self)
            new_conference.conference_id = conference_id
            conf_port = get_free_port()
            new_conference.conf_serve_port = conf_port
            text_port = get_free_port()
            new_conference.text_serve_port = text_port
            video_port = get_free_port()
            new_conference.video_serve_port = video_port
            audio_port = get_free_port()
            new_conference.audio_serve_port = audio_port

            self.conference_servers[conference_id] = new_conference

            # 启动会议服务器作为异步任务
            asyncio.create_task(new_conference.start())
            print(f"Conference {conference_id} started on port {conf_port} with the host {client_address}")

            return {
                "status": "success",
                "conference_id": conference_id,
                "conf_port": conf_port,
                "text_port": text_port,
                "video_port": video_port,
                "audio_port": audio_port
            }
        except Exception as e:
            print(f"Failed to create conference: {e}")
            return {"status": "error", "message": str(e)}

    async def handle_join_conference(self, conference_id):
        """
        Join conference: search corresponding conference_info and ConferenceServer,
        and reply necessary info to client.
        """
        try:
            # 检查会议是否存在
            if conference_id not in self.conference_servers:
                print(f"Conference {conference_id} not found.")
                return {
                    "status": "error",
                    "message": f"Conference {conference_id} does not exist."
                }

            # 获取对应的 ConferenceServer 实例
            conference_server = self.conference_servers[conference_id]

            print(f"Client joined conference {conference_id} on port {conference_server.conf_serve_port}.")
            return {
                "status": "success",
                "conference_id": conference_id,
                "conf_port": conference_server.conf_serve_port,
                "text_port": conference_server.text_serve_port,
                "video_port": conference_server.video_serve_port,
                "audio_port": conference_server.audio_serve_port
            }
        except Exception as e:
            print(f"Error while joining conference {conference_id}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def handle_quit_conference(self):
        """
        quit conference (in-meeting request & or no need to request)
        """
        pass

    def handle_cancel_conference(self):
        """
        cancel conference (in-meeting request, a ConferenceServer should be closed by the MainServer)
        """
        pass

    async def request_handler(self, reader, writer):
        """
        Handle out-meeting (or also in-meeting) requests from clients.
        """
        try:
            client_address = writer.get_extra_info('peername')
            print(f"Connected to client {client_address}")

            # 标志变量：是否保持连接
            keep_connection = True

            while keep_connection:
                # 读取客户端请求数据
                data = await reader.read(1024)
                if not data:
                    print(f"Client {client_address} disconnected.")
                    break

                # 解码收到的数据
                message = data.decode()
                print(f"Received from {client_address}: {message}")

                # 解析请求数据（假设是 JSON 格式）
                try:
                    request = json.loads(message)
                    request_type = request.get("type")  # 请求类型
                    payload = request.get("data")  # 请求的具体数据
                except json.JSONDecodeError:
                    print("Invalid data format received.")
                    response = {"status": "error", "message": "Invalid request format."}
                    writer.write(json.dumps(response).encode())
                    await writer.drain()
                    continue

                # 根据请求类型执行相应的操作
                if request_type == "create_conference":
                    response = await self.handle_create_conference(client_address)
                elif request_type == "join_conference":
                    conference_id = payload.get("conference_id")
                    response = await self.handle_join_conference(conference_id)
                    # response = f"The existed meetings are: {[item[0] for item in self.conference_servers]}"
                elif request_type == "join_conference":
                    conference_id = payload.get("conference_id")
                    response = await self.handle_join_conference(conference_id)
                elif request_type == "search_conference":
                    if len(self.conference_servers)==0:
                        response="There is no existed meeting."
                    else:
                        response = f"The existed meetings are: {[item for item in self.conference_servers]}"
                elif request_type == "quit_conference":
                    response = self.handle_quit_conference(payload)
                elif request_type == "cancel_conference":
                    # 客户端发送 cancel，断开连接
                    response = {"status": "success", "message": "Connection closed by client request."}
                    writer.write(json.dumps(response).encode())
                    await writer.drain()
                    keep_connection = False  # 设置标志为 False，退出循环
                    break
                else:
                    response = {"status": "error", "message": f"Unknown request type: {request_type}"}

                # 返回响应给客户端
                writer.write(json.dumps(response).encode())
                await writer.drain()

        except Exception as e:
            print(f"Error handling client {client_address}: {e}")
        finally:
            # 如果连接已经断开，清理资源
            print(f"Closing connection to client {client_address}.")
            writer.close()
            # await writer.wait_closed()
            print(f"Connection to client {client_address} fully closed.")

    async def start(self):
        """
        Start MainServer and begin listening for client connections.
        """
        try:
            print(f"Starting MainServer on {self.server_ip}:{self.server_port}")

            # 创建异步服务器，监听客户端连接
            # loop = asyncio.get_event_loop()
            self.main_server = await asyncio.start_server(self.request_handler, self.server_ip, self.server_port)

            # 启动服务器并开始监听
            # server = loop.run_until_complete(self.main_server)
            print(f"MainServer is running and listening on {self.main_server.sockets[0].getsockname()}")

            await self.main_server.serve_forever()

            # # 持续运行事件循环以处理客户端请求
            # try:
            #     loop.run_forever()
            # except KeyboardInterrupt:
            #     print("Server shutting down due to keyboard interrupt.")
            # finally:
            #     # 关闭服务器并清理资源
            #     self.main_server.close()
            #     loop.run_until_complete(self.main_server.wait_closed())
            #     loop.close()
            #     print("MainServer has been stopped.")
        except Exception as e:
            print(f"Failed to start MainServer: {e}")

SERVER_IP='127.0.0.1'
MAIN_SERVER_PORT=8888
if __name__ == '__main__':
    server = MainServer(SERVER_IP, MAIN_SERVER_PORT)
    asyncio.run(server.start())
