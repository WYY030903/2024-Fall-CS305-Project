import asyncio
import struct
import socket
from collections import defaultdict

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
        if len(data) < 6:
            print("Invalid data packet received.")
            return  # 无效的数据包

        header = data[:6]
        payload = data[6:]
        received_chunk_id, sequence_number, total_packets = struct.unpack("!HHH", header)

        # # 将音频数据保存到缓冲区
        # if received_chunk_id not in self.server.audio_buffers:
        #     self.server.audio_buffers[received_chunk_id] = {}

        client_buffer = self.server.audio_buffers[addr].get(received_chunk_id, {})
        client_buffer[sequence_number] = payload
        self.server.audio_buffers[addr][received_chunk_id] = client_buffer

        if len(client_buffer) == total_packets:
            # one client's chunk
            sorted_payloads = [self.server.audio_buffers[received_chunk_id][i] for i in range(1, total_packets + 1)]
            audio_data = b"".join(sorted_payloads)
            del self.server.audio_buffers[received_chunk_id]

            # put client's chunk into queue
            self.server.audio_data_queue.put_nowait((addr, audio_data))

            del self.server.audio_buffers[addr][received_chunk_id]

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")


class AudioServer:
    def __init__(self, input_ip, input_port, output_port):
        self.clients = None
        self.input_ip = input_ip
        self.input_port = input_port
        self.output_ip = input_ip
        self.output_port = output_port
        self.audio_buffers = defaultdict(dict)
        self.audio_data_queue = asyncio.Queue()

        self.stream_chunk_id = 0

    async def send_mixed_audio(self):
        while True:
            user_audio_data = {}

            # 收集来自不同用户的完整音频数据
            while not self.audio_data_queue.empty():
                addr, audio_data = await self.audio_data_queue.get()
                user_audio_data[addr] = audio_data

            if not user_audio_data:
                await asyncio.sleep(0.01)  # 避免空循环占用资源
                continue

            # 对音频数据进行混音
            mixed_audio = None
            for addr, audio_data in user_audio_data.items():
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                if mixed_audio is None:
                    mixed_audio = audio_array
                else:
                    mixed_audio = np.clip(mixed_audio + audio_array, -32768, 32767)

            if mixed_audio is None:
                continue

            # 将混音数据分片并发送给所有客户端
            mixed_audio_data = mixed_audio.astype(np.int16).tobytes()
            total_packets = (len(mixed_audio_data) + CHUNK_SIZE - 1) // CHUNK_SIZE

            for i in range(total_packets):
                # 提取分片数据
                payload = mixed_audio_data[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
                header = struct.pack("!HHH", self.stream_chunk_id, i + 1, total_packets)
                packet = header + payload

                # 发送分片到所有客户端
                for client_addr in self.clients:
                    self.transport.sendto(packet, client_addr)

            # 更新 chunk_id（避免溢出）
            self.stream_chunk_id = (self.stream_chunk_id + 1) % 65536
        # while True:
        #     mixed_audio = None
        #     user_audio_data = {}
        #
        #     while True:
        #         try:
        #             addr, audio_data = self.audio_data_queue.get_nowait()
        #             user_audio_data[addr] = audio_data
        #         except asyncio.QueueEmpty:
        #             break
        #
        #     if not user_audio_data:
        #         await asyncio.sleep(0.01)  # avoid CPU waste
        #         continue
        #
        #     # mix chunks from all clients
        #     for addr, audio_data in user_audio_data.items():
        #         audio_array = np.frombuffer(audio_data, dtype=np.int16)
        #         if mixed_audio is None:
        #             mixed_audio = audio_array
        #         else:
        #             mixed_audio = np.clip(mixed_audio + audio_array, -32768, 32767)
        #
        #     mixed_audio_data = mixed_audio.astype(np.int16).tobytes()
        #
        #     self.transport.sendto(mixed_audio_data, (self.output_ip, self.output_port))

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
