import asyncio
import pyaudio
import numpy as np
import socket
import struct

MAX_UDP_PACKET_SIZE = 1024  # UDP最大数据包大小
AUDIO_FORMAT = pyaudio.paInt16  # 音频格式：16位整数
CHANNELS = 1  # 单声道
RATE = 16000  # 采样率：16kHz
CHUNK_SIZE = 1024  # 每次读取的音频块大小


class UDPSenderProtocol:
    def __init__(self, server_ip, server_port, audio_queue):
        self.server_ip = server_ip
        self.server_port = server_port
        self.audio_queue = audio_queue
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        print(f"Sending audio to {self.server_ip}:{self.server_port}")

    def datagram_sent(self, data, addr):
        pass  # 可添加日志或统计数据发送量

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP sender connection closed")


async def capture_and_send_audio(loop, server_ip, server_port):
    audio_queue = asyncio.Queue()

    # 初始化 PyAudio 捕获音频
    p = pyaudio.PyAudio()
    stream = p.open(format=AUDIO_FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK_SIZE)

    def capture_audio():
        try:
            while True:
                # 从麦克风捕获音频数据
                audio_data = stream.read(CHUNK_SIZE)
                audio_queue.put_nowait(audio_data)
        except Exception as e:
            print(f"Error capturing audio: {e}")

    # 启动捕获音频线程
    import threading
    capture_thread = threading.Thread(target=capture_audio, daemon=True)
    capture_thread.start()

    # 创建 UDP 发送端点
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPSenderProtocol(server_ip, server_port, audio_queue),
        remote_addr=(server_ip, server_port)
    )

    try:
        while True:
            audio_data = await audio_queue.get()
            if audio_data is None:
                break  # 停止捕获

            # 分片发送音频数据
            audio_size = len(audio_data)
            total_packets = (audio_size + MAX_UDP_PACKET_SIZE - 1) // MAX_UDP_PACKET_SIZE
            for seq_num in range(1, total_packets + 1):
                start = (seq_num - 1) * MAX_UDP_PACKET_SIZE
                end = start + MAX_UDP_PACKET_SIZE
                packet_part = audio_data[start:end]

                # 构建包头：序列号（1-based），总包数
                header = struct.pack("!HH", seq_num, total_packets)
                transport.sendto(header + packet_part)

    except Exception as e:
        print(f"Error occurred during audio sending: {e}")
    finally:
        transport.close()
        print("Audio sending stopped.")


class UDPReceiverProtocol:
    def __init__(self, audio_queue):
        self.audio_queue = audio_queue
        self.transport = None
        self.audio_buffer = {}

    def connection_made(self, transport):
        self.transport = transport
        print("Listening for audio...")

    def datagram_received(self, data, addr):
        if len(data) < 4:
            return  # 无效的数据包

        header = data[:4]
        payload = data[4:]
        sequence_number, total_packets = struct.unpack("!HH", header)

        if total_packets not in self.audio_buffer:
            self.audio_buffer[total_packets] = {}

        self.audio_buffer[total_packets][sequence_number] = payload

        if len(self.audio_buffer[total_packets]) == total_packets:
            # 重组完整音频数据
            sorted_payloads = [self.audio_buffer[total_packets][i] for i in range(1, total_packets + 1)]
            audio_data = b"".join(sorted_payloads)
            del self.audio_buffer[total_packets]

            # 将音频数据放入队列
            self.audio_queue.put_nowait(audio_data)

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")


async def receive_and_play_audio(loop, local_port):
    audio_queue = asyncio.Queue()

    # 创建 UDP 接收端点
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: UDPReceiverProtocol(audio_queue),
        local_addr=("0.0.0.0", local_port)
    )

    # 初始化 PyAudio 播放音频
    p = pyaudio.PyAudio()
    stream = p.open(format=AUDIO_FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    frames_per_buffer=CHUNK_SIZE)

    async def play_audio():
        try:
            while True:
                audio_data = await audio_queue.get()
                if audio_data is None:
                    break

                # 播放接收到的音频数据
                stream.write(audio_data)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    # 播放接收到的音频
    await play_audio()

    transport.close()
    print("Stopped receiving and playing audio.")


# async def main():
#     server_ip = "127.0.0.1"  # 服务器 IP
#     server_port = 5001  # 音频服务器端口
#     local_port = 5002  # 本地接收音频的端口
#
#     loop = asyncio.get_running_loop()
#
#     # 创建音频发送和接收任务
#     send_task = asyncio.create_task(capture_and_send_audio(loop, server_ip, server_port))
#     receive_task = asyncio.create_task(receive_and_play_audio(loop, local_port))
#
#     # 并行运行两个任务
#     await asyncio.gather(send_task, receive_task)

# 启动客户端
# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except KeyboardInterrupt:
#         print("Client stopped by user.")
