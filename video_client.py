import cv2
import asyncio
import struct
import numpy as np
import threading

MAX_UDP_PACKET_SIZE = 4096  # UDP 最大数据包大小

class UDPSenderProtocol:
    def __init__(self, server_ip, server_port, frame_queue):
        self.server_ip = server_ip
        self.server_port = server_port
        self.frame_queue = frame_queue
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        print(f"Sending video to {self.server_ip}:{self.server_port}")

    def datagram_sent(self, data, addr):
        pass  # 可添加日志或统计数据发送量

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP sender connection closed")

class VideoCaptureHandler:
    def __init__(self, loop, server_ip, server_port,receive_port):
        self.loop = loop
        self.server_ip = server_ip
        self.server_port = server_port
        self.receive_port= receive_port
        self.cap = None
        self.frame_queue = asyncio.Queue(maxsize=20)
        self.send_video_task = None
        self.cancelled = False  # 用于标记是否取消任务
        self.capture_transport=None
        self.receive_transport=None
    
    async def capture_frames(self):
        """
        异步捕获视频帧并放入队列中。
        """
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("Error: Could not open video capture.")
            await self.frame_queue.put(None)  # 向队列中放入结束标志
            return

        try:
            while not self.cancelled:
                # 使用 run_in_executor 运行阻塞的 read 操作
                ret, frame = await self.loop.run_in_executor(None, self.cap.read)
                if not ret:
                    print("Error: Failed to capture frame.")
                    await self.frame_queue.put(None)
                    break

                # 压缩视频帧为 JPEG 格式，降低质量以减少 CPU 负担
                success, encoded_frame = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 10])
                if not success:
                    continue
                frame_bytes = encoded_frame.tobytes()

                # 将帧数据放入队列中
                await self.frame_queue.put(frame_bytes)

                # 控制帧率
                await asyncio.sleep(1 / 20)  # 20 FPS
        finally:
            if self.cap:
                self.cap.release()
            # 向队列中放入结束标志，以通知发送任务结束
            await self.frame_queue.put(None)

    async def send_frames(self, p2p_socket=None, p2p_target=None):
        """
        异步从队列中获取视频帧并通过 UDP 发送到服务器或 P2P。
        """
        # 创建 UDP 发送端点
        transport, self.protocol = await self.loop.create_datagram_endpoint(
            lambda: UDPSenderProtocol(self.server_ip, self.server_port,self.frame_queue),
            remote_addr=(self.server_ip, self.server_port)
        )
        print("UDP capture endpoint created.")

        try:
            while not self.cancelled:
                frame_bytes = await self.frame_queue.get()
                if frame_bytes is None:
                    break  # 结束捕获

                frame_size = len(frame_bytes)
                total_packets = (frame_size + MAX_UDP_PACKET_SIZE - 1) // MAX_UDP_PACKET_SIZE
                for sequence_number in range(1, total_packets + 1):
                    start = (sequence_number - 1) * MAX_UDP_PACKET_SIZE
                    end = start + MAX_UDP_PACKET_SIZE
                    packet = frame_bytes[start:end]

                    # 构建包头：序列号（1-based），总包数
                    header = struct.pack("!HH", sequence_number, total_packets)
                    if p2p_socket and p2p_target:
                        # 在 P2P 模式下直接发送数据
                        p2p_socket.sendto(header + packet, p2p_target)
                    else:
                        # 非 P2P 模式，发送到服务器
                        transport.sendto(header + packet)
        except Exception as e:
            print(f"Error occurred during video sending: {e}")
        finally:
            transport.abort()
            print("Video sending stopped.")

    async def capture_and_send(self, p2p_socket=None, p2p_target=None):
        """
        启动并行的捕获和发送任务，实现实时视频功能。
        """
        capture_task = asyncio.create_task(self.capture_frames())
        send_task = asyncio.create_task(self.send_frames(p2p_socket, p2p_target))

        # 等待任意一个任务完成
        done, pending = await asyncio.wait(
            [capture_task, send_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        # 如果有任务完成，取消其他任务
        for task in pending:
            task.cancel()

        # 处理已完成的任务以获取异常
        for task in done:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"Task encountered an exception: {e}")

    def display_frame(self, frame):
        """
        显示接收到的视频帧。
        """
        cv2.imshow('Received Video1', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            # 按 'q' 键退出
            self.cancelled = True
            asyncio.create_task(self.stop_video())

    async def receive_and_display(self):
        """
        完全异步的接收视频帧并显示的函数。
        """
        # 创建 UDP 接收端点
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: UDPReceiverProtocol(self.display_frame),
            local_addr=("0.0.0.0", self.receive_port)
        )
        print(f"UDP receiver started on port {self.receive_port}")

        try:
            # 等待取消信号
            while not self.cancelled:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # 捕获取消信号时执行清理操作
            print("Receive and display task cancelled.")
        finally:
            # 关闭 UDP 传输
            if transport:
                transport.abort()
                print("UDP transport closed.")
            
            # 关闭 OpenCV 窗口
            cv2.destroyAllWindows()
            print("Stopped receiving video.")
        
        
    async def start_video(self, p2p_socket=None, p2p_target=None):
        """
        启动视频捕获、发送和接收显示任务
        """
        self.cancelled = False
        """启动视频捕获、发送和接收显示任务"""
        if self.send_video_task:
            print("Video tasks are already running.")
            return

        print("Starting video capture, send, and receive tasks.")

        try:
            # 启动视频捕获和发送任务
            self.send_video_task = asyncio.create_task(self.capture_and_send(p2p_socket, p2p_target))
            
            # 启动视频接收和显示任务
            self.receive_video_task = asyncio.create_task(self.receive_and_display())

            # 等待所有任务完成
            await asyncio.gather(self.send_video_task, self.receive_video_task)

        except Exception as e:
            print(f"Error occurred while starting the video task: {e}")

    async def stop_video(self):
        """停止视频捕获、发送和接收任务"""
        if self.send_video_task:
            print("Stopping video capture and send task...")
            self.cancelled = True  # 设置为取消状态

            # 取消发送任务
            self.send_video_task.cancel()

            try:
                # 等待发送任务完成，捕获异常
                await self.send_video_task
            except asyncio.CancelledError:
                print("Video capture and send task was successfully cancelled.")

            self.send_video_task = None  # 清空发送任务引用

            # 确保视频捕获资源被释放
            if self.cap is not None:
                print("Stopping video capture...")
                self.cap.release()  # 释放视频捕获资源
                self.cap = None
            print("Video capture and send task stopped.")

        else:
            print("No video capture task to stop.")
        
        # 停止接收任务
        if self.receive_video_task:
            print("Stopping video receive task...")
            self.receive_video_task.cancel()

            try:
                # 等待接收任务完成，捕获异常
                await self.receive_video_task
            except asyncio.CancelledError:
                print("Video receive task was successfully cancelled.")

            self.receive_video_task = None  # 清空接收任务引用
            print("Video receive task stopped.")
        else:
            print("No video receive task to stop.")
            
        # 关闭 OpenCV 窗口
        cv2.destroyAllWindows()
        cv2.waitKey(1)

class UDPReceiverProtocol:
    def __init__(self, display_callback):
        self.display_callback = display_callback
        self.transport = None
        self.video_buffer = {}

    def connection_made(self, transport):
        self.transport = transport
        print("Listening for video...")

    def datagram_received(self, data, addr): 
        # print(f"Received data from {addr}, size: {len(data)}")
        if len(data) < 4:
            return  # 无效的数据包

        header = data[:4]
        payload = data[4:]
        sequence_number, total_packets = struct.unpack("!HH", header)

        if total_packets not in self.video_buffer:
            self.video_buffer[total_packets] = {}

        self.video_buffer[total_packets][sequence_number] = payload

        if len(self.video_buffer[total_packets]) == total_packets:
            # 重组完整帧
            sorted_payloads = [self.video_buffer[total_packets][i] for i in range(1, total_packets + 1)]
            frame_data = b"".join(sorted_payloads)
            del self.video_buffer[total_packets]

            # 解码并显示帧
            frame_array = np.frombuffer(frame_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
            if frame is not None:
                self.display_callback(frame)

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")

# async def receive_and_display(loop, receive_port):
#     """
#     完全异步的接收视频帧并显示的函数。
#     """
#     # 使用队列来传递帧
#     frame_queue = asyncio.Queue()

#     # 用来显示帧的异步任务
#     async def show_frames():
#         try:
#             while True:
#                 frame = await frame_queue.get()
#                 if frame is None:  # 如果帧为 None，表示结束
#                     break

#                 # 显示帧
#                 cv2.imshow("Combined Video Stream client", frame)
#                 if cv2.waitKey(1) & 0xFF == ord('q'):
#                     break
#         finally:
#             cv2.destroyAllWindows()

#     # 在普通模式下创建 UDP 接收端点
#     transport, protocol = await loop.create_datagram_endpoint(
#         lambda: UDPReceiverProtocol(lambda data: asyncio.create_task(handle_received_frame(data, frame_queue))),
#         local_addr=("0.0.0.0", receive_port)
#     )

#     # 处理接收到的视频帧
#     async def handle_received_frame(data, frame_queue):
#         # 假设数据包包含帧数据
#         try:
#             if len(data) < 4:
#                 return  # 无效数据包

#             # 解析头部（假设头部包括序号、总包数等）
#             header = data[:4]
#             payload = data[4:]
#             sequence_number, total_packets = struct.unpack("!HH", header)

#             # 重新组装完整的帧数据
#             # 这里简单的做了一个处理，实际上需要实现一个缓存来存储和拼接帧数据
#             frame_data = payload  # 假设每个数据包都是单独一帧
#             frame_array = np.frombuffer(frame_data, dtype=np.uint8)
#             frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)

#             if frame is not None:
#                 # 将帧放入队列
#                 await frame_queue.put(frame)
#         except Exception as e:
#             print(f"Error while processing frame: {e}")

#     # 启动显示帧的任务
#     await show_frames()

#     # 关闭 transport
#     transport.close()
#     print("Stopped receiving video.")
