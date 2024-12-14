import asyncio

# 用来存储所有连接的WebSocket
connections = []


async def broadcast(message):
    # 广播消息给所有连接的客户端
    for writer in connections:
        writer.write(message.encode())
        await writer.drain()


async def handle_text_socket(reader, writer):
    # 处理聊天文本socket
    while True:
        data = await reader.read(100)
        if not data:
            break
        print(f"Received text data: {data.decode()}")
        await broadcast(f"Text message: {data.decode()}")
    writer.close()


async def handle_video_socket(reader, writer):
    # 处理视频socket
    while True:
        data = await reader.read(100)
        if not data:
            break
        print(f"Received video data: {data.decode()}")
        await broadcast(f"Video message: {data.decode()}")
    writer.close()


async def handle_audio_socket(reader, writer):
    # 处理音频socket
    while True:
        data = await reader.read(100)
        if not data:
            break
        print(f"Received audio data: {data.decode()}")
        await broadcast(f"Audio message: {data.decode()}")
    writer.close()


async def handle_client(reader, writer):
    # 启动不同的处理函数，不需要在此判断数据类型
    connections.append(writer)

    # 启动三个协程来处理不同的socket类型
    asyncio.create_task(handle_text_socket(reader, writer))
    asyncio.create_task(handle_video_socket(reader, writer))
    asyncio.create_task(handle_audio_socket(reader, writer))


async def start_server(host='127.0.0.1', port=8888):
    server = await asyncio.start_server(
        handle_client,
        host,
        port
    )
    addr = server.sockets[0].getsockname()
    print(f'Serving on {addr}')
    async with server:
        await server.serve_forever()


# 启动服务器
asyncio.run(start_server())

