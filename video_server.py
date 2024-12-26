import cv2
import asyncio
import struct
import numpy as np
import math
import socket
from collections import defaultdict

MAX_UDP_PACKET_SIZE = 1024  # UDP 最大数据包大小

class VideoServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, server):
        self.transport = None
        self.server = server

    def connection_made(self, transport):
        self.transport = transport
        print("UDP server is up and listening.")

    def datagram_received(self, data, addr):
        if len(data) < 6:
            print(f"Received invalid packet from {addr}")
            return  # 无效的数据包

        # 解析包头
        header = data[:6]
        payload = data[6:]
        received_frame_id, sequence_number, total_packets = struct.unpack("!HHH", header)

        # 获取或创建该客户端的缓冲区
        client_buffer = self.server.video_buffers[addr].get(received_frame_id, {})
        client_buffer[sequence_number] = payload
        self.server.video_buffers[addr][received_frame_id] = client_buffer

        # 检查是否接收到了所有分片
        if len(client_buffer) == total_packets:
            # 重组完整帧
            sorted_payloads = [
                self.server.video_buffers[addr][received_frame_id][i]
                for i in sorted(self.server.video_buffers[addr][received_frame_id].keys())
            ]
            frame_data = b"".join(sorted_payloads)

            # 解码视频帧
            frame_array = np.frombuffer(frame_data, dtype=np.uint8)
            frame = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)

            if frame is not None:
                self.server.client_frames[addr] = frame
                print(f"Received complete frame from {addr}")
            else:
                print(f"Failed to decode frame from {addr}")

            # 清空该帧的缓冲区
            del self.server.video_buffers[addr][total_packets]


class VideoServer:
    def __init__(self, server_ip, server_port, unicast_port):
        self.server_ip = server_ip
        self.server_port = server_port
        self.unicast_port = unicast_port

        # 数据结构
        self.video_buffers = defaultdict(dict)  # {client_address: {total_packets: {sequence_number: payload}}}
        self.client_frames = {}  # {client_address: frame}
        self.client_addresses = set()

        # 创建 UDP 发送套接字
        self.unicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.stream_frame_id = 0

    def get_combined_frame(self, client_frames):
        """
        拼接所有客户端的视频帧。
        :param client_frames: 每个客户端的最新视频帧，格式为 {client_address: frame}
        :return: 拼接后的帧
        """
        frames = list(client_frames.values())
        num_frames = len(frames)

        if num_frames == 0:
            return None

        # 动态计算布局（例如 1xN、2xN 网格）
        grid_size = math.ceil(math.sqrt(num_frames))  # 网格大小，例如 2x2、3x3
        blank_frame = np.zeros((240, 320, 3), dtype=np.uint8)  # 空白帧，固定大小

        # 调整所有帧的大小到 320x240
        resized_frames = []
        for frame in frames:
            resized_frame = cv2.resize(frame, (320, 240))  # 将帧缩放到固定大小
            resized_frames.append(resized_frame)

        # 填充帧列表，使其能够完全填充网格
        while len(resized_frames) < grid_size ** 2:
            resized_frames.append(blank_frame)

        # 将帧重组为网格
        rows = []
        for i in range(0, len(resized_frames), grid_size):
            row = np.hstack(resized_frames[i:i + grid_size])  # 按行水平拼接
            rows.append(row)
        combined_frame = np.vstack(rows)  # 按行垂直拼接

        return combined_frame

    async def broadcast_combined_frame(self):
        """
        定期拼接所有客户端的最新帧，并发送回所有客户端。
        """
        while True:
            await asyncio.sleep(1 / 20)  # 20 FPS

            if not self.client_frames:
                continue  # 没有可拼接的帧

            # 拼接所有客户端的视频帧
            combined_frame = self.get_combined_frame(self.client_frames)

            if combined_frame is None:
                continue

            # 编码拼接后的帧为 JPEG
            success, encoded_frame = cv2.imencode('.jpg', combined_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            if not success:
                print("Failed to encode combined frame")
                continue
            frame_bytes = encoded_frame.tobytes()
            total_packets = math.ceil(len(frame_bytes) / MAX_UDP_PACKET_SIZE)

            # 发送拼接后的帧到所有客户端
            for client_address in self.client_frames.keys():
                self.client_addresses.add(client_address)
                target_address = (client_address[0], self.unicast_port)  # 使用客户端 IP 和接收端口

                # 分片发送
                for seq_num in range(1, total_packets + 1):
                    start = (seq_num - 1) * MAX_UDP_PACKET_SIZE
                    end = start + MAX_UDP_PACKET_SIZE
                    packet_part = frame_bytes[start:end]

                    # 构建包头：序列号（1-based），总包数
                    header = struct.pack("!HHH", self.stream_frame_id, seq_num, total_packets)
                    self.unicast_socket.sendto(header + packet_part, target_address)

            self.stream_frame_id += 1
            if self.stream_frame_id > 3000:
                self.stream_frame_id = 0

            # 在服务器端显示拼接后的帧
            # cv2.imshow("Combined Video Stream", combined_frame)
            # if cv2.waitKey(1) & 0xFF == ord('q'):
            #     print("Shutting down server...")
            #     break

    async def run(self):
        loop = asyncio.get_running_loop()

        # 创建 UDP 服务器
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: VideoServerProtocol(self),
            local_addr=(self.server_ip, self.server_port)
        )

        # 启动广播任务
        broadcast_task = asyncio.create_task(self.broadcast_combined_frame())

        try:
            await broadcast_task
        finally:
            transport.close()
            self.unicast_socket.close()
            cv2.destroyAllWindows()
            print("Server shutdown complete.")

# if __name__ == "__main__":
#     import socket

#     server_ip = "10.27.89.235"  # 监听地址
#     server_port = 5000           # 接收客户端视频的端口
#     unicast_port = 6000          # 单播拼接视频的端口

#     server = VideoServer(server_ip, server_port, unicast_port)

#     try:
#         asyncio.run(server.run())
#     except KeyboardInterrupt:
#         print("Server stopped by user.")
