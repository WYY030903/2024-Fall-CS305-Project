import asyncio
import struct
import socket
import pyaudio
import numpy as np

MAX_UDP_PACKET_SIZE = 1024
AUDIO_FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK_SIZE = 1024

class AudioServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, server):
        self.server = server

    def connection_made(self, transport):
        self.transport = transport
        print("UDP server is up and listening for incoming audio data.")

    def datagram_received(self, data, addr):
        if len(data) < 4:
            print("Invalid data packet received.")
            return  # 无效的数据包

        header = data[:4]
        payload = data[4:]
        sequence_number, total_packets = struct.unpack("!HH", header)

        # 将音频数据保存到缓冲区
        if total_packets not in self.server.audio_buffers:
            self.server.audio_buffers[total_packets] = {}

        self.server.audio_buffers[total_packets][sequence_number] = payload

        if len(self.server.audio_buffers[total_packets]) == total_packets:
            # 重组完整音频数据
            sorted_payloads = [self.server.audio_buffers[total_packets][i] for i in range(1, total_packets + 1)]
            audio_data = b"".join(sorted_payloads)
            del self.server.audio_buffers[total_packets]

            # 将音频数据混合并放入队列等待发送
            self.server.audio_data_queue.put_nowait(audio_data)

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")

class AudioServer:
    def __init__(self, input_ip, input_port, output_port):
        self.input_ip = input_ip
        self.input_port = input_port
        self.output_ip = input_ip
        self.output_port = output_port
        self.audio_buffers = {}
        self.audio_data_queue = asyncio.Queue()

    async def send_mixed_audio(self):
        while True:
            # 等待获取混合后的音频数据
            audio_data = await self.audio_data_queue.get()
            # 发送音频数据到输出端口
            self.transport.sendto(audio_data, (self.output_ip, self.output_port))

    async def run(self):
        loop = asyncio.get_running_loop()
        # 创建输入端口的 UDP 服务器端点
        self.transport, protocol = await loop.create_datagram_endpoint(
            lambda: AudioServerProtocol(self),
            local_addr=(self.input_ip, self.input_port)
        )

        # 启动音频转发任务
        forward_task = asyncio.create_task(self.send_mixed_audio())

        try:
            await forward_task
        except asyncio.CancelledError:
            print("Audio server task was cancelled.")
        except Exception as e:
            print(f"Error in audio server: {e}")
        finally:
            self.transport.close()
            print("Audio server shutdown complete.")