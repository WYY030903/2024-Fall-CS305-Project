import cv2
import asyncio
import struct
import numpy as np

MAX_UDP_PACKET_SIZE = 4096  # UDP 最大数据包大小

frame_id = 0

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

    def pause_writing(self):
        print("Flow control: pause writing")

    def resume_writing(self):
        print("Flow control: resume writing")


async def capture_and_send(loop, server_ip, server_port, p2p_socket=None, p2p_target=None):
    """
    捕获视频帧并通过 UDP 发送到服务器。
    """
    # 创建一个队列用于传递帧
    global frame_id
    frame_queue = asyncio.Queue(maxsize=10)

    # 启动视频捕获线程
    def capture_frames():
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("Error: Could not open video capture.")
            asyncio.run_coroutine_threadsafe(frame_queue.put(None), loop)
            return

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    print("Error: Failed to capture frame.")
                    asyncio.run_coroutine_threadsafe(frame_queue.put(None), loop)
                    break

                # 压缩视频帧为 JPEG 格式，降低质量以减少 CPU 负担
                success, encoded_frame = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 10])
                if not success:
                    continue

                frame_bytes = encoded_frame.tobytes()
                asyncio.run_coroutine_threadsafe(frame_queue.put(frame_bytes), loop)

                # 控制帧率
                asyncio.run_coroutine_threadsafe(asyncio.sleep(1 / 20), loop)  # 20 FPS
        finally:
            cap.release()

    import threading
    capture_thread = threading.Thread(target=capture_frames, daemon=True)
    capture_thread.start()

    # 创建 UDP 发送端点
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPSenderProtocol(server_ip, server_port, frame_queue),
        remote_addr=(server_ip, server_port)
    )

    try:
        while True:
            frame_bytes = await frame_queue.get()
            if frame_bytes is None:
                break  # 结束捕获

            frame_size = len(frame_bytes)
            total_packets = (frame_size + MAX_UDP_PACKET_SIZE - 1) // MAX_UDP_PACKET_SIZE
            for sequence_number in range(1, total_packets + 1):
                start = (sequence_number - 1) * MAX_UDP_PACKET_SIZE
                end = start + MAX_UDP_PACKET_SIZE
                packet = frame_bytes[start:end]

                # 构建包头：序列号（1-based），总包数, frame_id
                header = struct.pack("!HH", sequence_number, total_packets, frame_id)
                if p2p_socket and p2p_target:
                    # 在 P2P 模式下直接发送数据
                    p2p_socket.sendto(header + packet, p2p_target)
                else:
                    # 非 P2P 模式，发送到服务器
                    transport.sendto(header + packet)
            frame_id = frame_id + 1
            if frame_id > 3000:
                frame_id = 0
    except Exception as e:
        print(f"Error occurred during video sending: {e}")
    finally:
        transport.close()
        print("Video sending stopped.")


class UDPReceiverProtocol:
    def __init__(self, display_callback):
        self.display_callback = display_callback
        self.transport = None
        self.video_buffer = {}

    def connection_made(self, transport):
        self.transport = transport
        print("Listening for video...")

    def datagram_received(self, data, addr):
        if len(data) < 5:
            return  # 无效的数据包

        header = data[:5]
        payload = data[5:]
        sequence_number, total_packets, received_frame_id = struct.unpack("!HH", header)

        if received_frame_id not in self.video_buffer:
            self.video_buffer[received_frame_id] = {}

        self.video_buffer[received_frame_id][sequence_number] = payload

        # 检查是否接收到完整帧
        if len(self.video_buffer[received_frame_id]) == total_packets:
            # 重组完整帧
            sorted_payloads = [self.video_buffer[received_frame_id][i] for i in range(1, total_packets + 1)]
            frame_data = b"".join(sorted_payloads)
            del self.video_buffer[received_frame_id]

            # 解码并显示帧
            frame_array = np.frombuffer(frame_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
            if frame is not None:
                self.display_callback(frame)
        else:
            # 丢弃不完整帧
            pass
            # print(f"Incomplete frame received from {addr}: {len(self.video_buffer[total_packets])}/{total_packets}")

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")


async def receive_and_display(loop, receive_port):
    """
    接收服务器返回的拼接视频帧并显示。
    """
    # 使用线程安全的队列来传递帧给显示部分
    frame_queue = asyncio.Queue(maxsize=10)

    def display_frame(frame):
        # 将帧放入队列
        asyncio.run_coroutine_threadsafe(frame_queue.put(frame), loop)

    p2p_socket = False
    if p2p_socket:
        # P2P 模式下直接使用传入的 socket
        transport, protocol = None, None
        print("Using P2P socket for receiving video.")
    else:
        # 普通模式下创建 UDP 接收端点
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: UDPReceiverProtocol(display_frame),
            local_addr=("0.0.0.0", receive_port)
        )

    async def show_frames():
        try:
            while True:
                frame = await frame_queue.get()
                if frame is None:
                    break

                cv2.imshow("Combined Video Stream", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
        finally:
            cv2.destroyAllWindows()

    p2p_socket = False
    if p2p_socket:
        # 在 P2P 模式下，直接接收数据
        async def p2p_receive_loop():
            while True:
                data, _ = p2p_socket.recvfrom(MAX_UDP_PACKET_SIZE + 4)
                if len(data) < 4:
                    continue  # 无效数据包

                header = data[:4]
                payload = data[4:]
                sequence_number, total_packets = struct.unpack("!HH", header)

                if total_packets not in protocol.video_buffer:
                    protocol.video_buffer[total_packets] = {}

                protocol.video_buffer[total_packets][sequence_number] = payload

                if len(protocol.video_buffer[total_packets]) == total_packets:
                    # 重组完整帧
                    sorted_payloads = [protocol.video_buffer[total_packets][i] for i in range(1, total_packets + 1)]
                    frame_data = b"".join(sorted_payloads)
                    del protocol.video_buffer[total_packets]

                    # 解码并显示帧
                    frame_array = np.frombuffer(frame_data, dtype=np.uint8)
                    frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
                    if frame is not None:
                        display_frame(frame)

        await asyncio.gather(show_frames(), p2p_receive_loop())
    else:
        await show_frames()
        transport.close()
        print("Stopped receiving video.")

# async def main():
#     server_ip = "10.27.89.235"  # 服务器 IP
#     server_port = 5000          # 服务器接收视频的端口
#     receive_port = 6000         # 客户端接收拼接视频的端口

#     loop = asyncio.get_running_loop()

#     # 创建发送和接收任务
#     send_task = asyncio.create_task(capture_and_send(loop, server_ip, server_port))
#     receive_task = asyncio.create_task(receive_and_display(loop, receive_port))

#     # 并行运行两个任务
#     await asyncio.gather(send_task, receive_task)

# # 启动客户端
# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         print("Client stopped by user.")
