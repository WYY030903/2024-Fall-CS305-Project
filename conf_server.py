import asyncio
# from util import *
import json
import socket

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
    def __init__(self, ):
        # async server
        self.conference_id = None  # conference_id for distinguish difference conference
        self.conf_serve_ports = None
        self.data_serve_ports = {}
        self.data_types = ['screen', 'camera', 'audio']  # example data types in a video conference
        self.clients_info = None
        self.client_conns = None
        self.mode = 'Client-Server'  # or 'P2P' if you want to support peer-to-peer conference mode

    async def handle_data(self, reader, writer, data_type):
        """
        running task: receive sharing stream data from a client and decide how to forward them to the rest clients
        从客户端接收数据并将其转发给会议中的其他与会者
        """
        try:
            while True:
                data = await reader.read(1024)  # Read data from client 从数据流中读取
                if not data:
                    break  # Client disconnected
                # Forward the data to other clients
                for client_writer in self.client_conns: #遍历连接会议的所有客户端
                    if client_writer is not writer: #排除将数据传回给自己的情况
                        client_writer.write(data)
                        await client_writer.drain()
        except Exception as e:
            print(f"Error handling data of type {data_type}: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def handle_client(self, reader, writer):
        """
        running task: handle the in-meeting requests or messages from clients
        """
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
                print(f"Received from {client_address}: {message}")

                # 解析请求数据（假设是 JSON 格式）需要统一请求数据的格式
                import json
                try:
                    request = json.loads(message)
                    request_type = request.get("type")  # 请求类型
                    payload = request.get("data")  # 请求的具体数据
                except json.JSONDecodeError:
                    print("Invalid data format received.")
                    continue

                # 根据请求类型执行相应的操作
                if request_type == "send_message":
                    # 转发文本消息
                    await self.broadcast_message(writer, payload, "text")
                elif request_type == "send_video":
                    # 开启视频流的处理
                    print(f"Starting video stream for {client_address}.")
                    await self.handle_data(reader, writer, "video")
                elif request_type == "send_audio":
                    # 开启音频流的处理
                    print(f"Starting audio stream for {client_address}.")
                    await self.handle_data(reader, writer, "audio")
                elif request_type == "exit":
                    # 客户端退出会议
                    print(f"Client {client_address} has exited the meeting.")
                    break
                else:
                    print(f"Unknown request type: {request_type}")
        except Exception as e:
            print(f"Error handling client {client_address}: {e}")
        finally:
            # 清理资源
            writer.close()
            await writer.wait_closed()
            print(f"Connection to client {client_address} closed.")

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

            # 通知所有客户端会议被取消
            cancellation_message = f"Conference {self.conference_id} has been canceled.".encode()
            for client_writer in self.client_conns:
                try:
                    client_writer.write(cancellation_message)
                    await client_writer.drain()
                    client_writer.close()
                    await client_writer.wait_closed()
                    print(f"Disconnected client from conference {self.conference_id}.")
                except Exception as e:
                    print(f"Failed to disconnect a client: {e}")

            # 清理客户端连接列表
            self.client_conns = []

            # 从服务器的会议列表中移除该会议
            if self.conference_id in self.conference_servers:
                del self.conference_servers[self.conference_id]
                print(f"Conference {self.conference_id} removed from active list.")

            print(f"Conference {self.conference_id} successfully canceled.")
        except Exception as e:
            print(f"Error while canceling conference {self.conference_id}: {e}")
        

    async def start(self):
        try:
            print(f"Starting ConferenceServer for conference ID: {self.conference_id} on port {self.conf_serve_ports}")
            self.running = True
            self.server = await asyncio.start_server(self.handle_client, '0.0.0.0', self.conf_serve_ports)
            print(f"ConferenceServer is now listening on port {self.conf_serve_ports}")

            async with self.server:
                await self.server.serve_forever()
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

            new_conference = ConferenceServer()
            new_conference.conference_id = conference_id
            port = get_free_port()
            new_conference.conf_serve_ports = port

            self.conference_servers[conference_id] = (new_conference, client_address)

            # 启动会议服务器作为异步任务
            asyncio.create_task(new_conference.start())
            print(f"Conference {conference_id} started on port {port} with the host {client_address}")

            return {"status": "success", "conference_id": conference_id, "port": port}
        except Exception as e:
            print(f"Failed to create conference: {e}")
            return {"status": "error", "message": str(e)}

    def handle_join_conference(self, conference_id):
        """
        Join conference: search corresponding conference_info and ConferenceServer,
        and reply necessary info to client.
        """
        try:
            # 检查会议是否存在
            if conference_id not in self.conference_servers:
                print(f"Conference {conference_id} not found.")
                return {"status": "error", "message": f"Conference {conference_id} does not exist."}

            # 获取对应的 ConferenceServer 实例
            conference_server = self.conference_servers[conference_id]
            port = conference_server.conf_serve_ports  # 获取会议的端口信息

            print(f"Client joined conference {conference_id} on port {port}.")
            return {"status": "success", "conference_id": conference_id, "port": port}
        except Exception as e:
            print(f"Error while joining conference {conference_id}: {e}")
            return {"status": "error", "message": str(e)}

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
                    response = f"The existed meetings are: {[item[0] for item in conference_servers]}"
                elif request_type == "search_conference":
                    response = f"The existed meetings are: {[item[0] for item in conference_servers]}"
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
            await writer.wait_closed()
            print(f"Connection to client {client_address} fully closed.")

    def start(self):
        """
        Start MainServer and begin listening for client connections.
        """
        try:
            print(f"Starting MainServer on {self.server_ip}:{self.server_port}")
            
            # 创建异步服务器，监听客户端连接
            loop = asyncio.get_event_loop()
            self.main_server = asyncio.start_server(self.request_handler, self.server_ip, self.server_port)

            # 启动服务器并开始监听
            server = loop.run_until_complete(self.main_server)
            print(f"MainServer is running and listening on {server.sockets[0].getsockname()}")

            # 持续运行事件循环以处理客户端请求
            try:
                loop.run_forever()
            except KeyboardInterrupt:
                print("Server shutting down due to keyboard interrupt.")
            finally:
                # 关闭服务器并清理资源
                server.close()
                loop.run_until_complete(server.wait_closed())
                loop.close()
                print("MainServer has been stopped.")
        except Exception as e:
            print(f"Failed to start MainServer: {e}")

SERVER_IP="10.12.36.251"
MAIN_SERVER_PORT=5000
if __name__ == '__main__':
    server = MainServer(SERVER_IP, MAIN_SERVER_PORT)
    server.start()
