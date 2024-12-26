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


class UDPSenderProtocol:
    def __init__(self, server_ip, server_port):
        self.server_ip = server_ip
        self.server_port = server_port
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

    def pause_writing(self):
        print("Flow control: pause writing")

    def resume_writing(self):
        print("Flow control: resume writing")


class AudioServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, server):
        self.transport = None
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
            sorted_payloads = [self.server.audio_buffers[addr][received_chunk_id][i] for i in
                               range(1, total_packets + 1)]
            audio_data = b"".join(sorted_payloads)
            del self.server.audio_buffers[addr][received_chunk_id]

            # put client's chunk into queue
            self.server.clients_audio_data[addr] = audio_data
            print("added")

    def error_received(self, exc):
        print(f"Error received: {exc}")

    def connection_lost(self, exc):
        print("UDP receiver connection closed")


class AudioServer:
    def __init__(self, server_ip, server_port, unicast_port):
        self.clients = None
        self.server_ip = server_ip
        self.server_port = server_port
        self.unicast_port = unicast_port
        self.audio_buffers = defaultdict(dict)
        self.clients_audio_data = {}

        self.unicast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.stream_chunk_id = 0
        self.sender_protocol = None

    async def send_mixed_audio(self):
        while True:
            if not self.clients_audio_data:
                print("no")
                continue

            print("yes")

            # 对音频数据进行混音
            mixed_audio = None
            for addr, audio_data in self.clients_audio_data.items():
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                if mixed_audio is None:
                    mixed_audio = audio_array
                else:
                    mixed_audio = np.clip(mixed_audio + audio_array, -32768, 32767)

            if mixed_audio is None:
                print("none")
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
                for addr in self.clients_audio_data.keys():
                    target_address = (addr[0], self.unicast_port)
                    self.unicast_socket.sendto(packet, target_address)

            # 更新 chunk_id（避免溢出）
            self.stream_chunk_id = (self.stream_chunk_id + 1) % 65536

    async def run(self):
        loop = asyncio.get_running_loop()
        # 创建输入端口的 UDP 服务器端点
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: AudioServerProtocol(self),
            local_addr=(self.server_ip, self.server_port)
        )

        # Create UDP sender for sending mixed audio
        # _, self.sender_protocol = await loop.create_datagram_endpoint(
        #     lambda: UDPSenderProtocol(),
        #     remote_addr=None
        # )

        print("ok")

        print("Audio server is running.")

        # 启动音频转发任务
        forward_task = asyncio.create_task(self.send_mixed_audio())

        try:
            await forward_task
        except asyncio.CancelledError:
            print("Audio server task was cancelled.")
        except Exception as e:
            print(f"Error in audio server: {e}")
        finally:
            transport.close()
            print("Audio server shutdown complete.")
