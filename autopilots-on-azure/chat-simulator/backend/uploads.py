import hashlib
import re

from fastapi import HTTPException, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header


def file_metadata(name, mime):
    if not isinstance(name, str) or not 1 <= len(name) <= 255 or any(
        ord(c) < 32 or ord(c) == 127 for c in name
    ):
        raise HTTPException(422, "Invalid filename")
    if not isinstance(mime, str) or not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", mime):
        mime = "application/octet-stream"
    return name, mime


def store_bytes(storage, file_id, payload, limit, name, mime):
    name, mime = file_metadata(name, mime)
    if len(payload) > limit:
        raise HTTPException(413, "File exceeds the 25 MB (26214400 bytes) limit")
    try:
        with storage.begin(file_id) as output:
            for start in range(0, len(payload), 256 * 1024):
                output.write(payload[start:start + 256 * 1024])
        storage.complete(file_id)
    except BaseException:
        storage.discard(file_id)
        raise
    return {"name": name, "size": len(payload), "content_type": mime,
            "sha256": hashlib.sha256(payload).hexdigest()}


async def receive_file(request: Request, storage, file_id, limit):
    content_type = request.headers.get("content-type", "")
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise HTTPException(422, "Use multipart/form-data with a file field")
    size = 0
    total = 0
    parts = 0
    finished = False
    name = None
    mime = "application/octet-stream"
    headers = {}
    header_name = bytearray()
    header_value = bytearray()
    header_bytes = 0
    digest = hashlib.sha256()
    output = storage.begin(file_id)

    def part_begin():
        nonlocal parts
        parts += 1
        if parts != 1:
            raise HTTPException(422, "Exactly one file field is required")

    def header_field(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 16 * 1024:
            raise HTTPException(422, "Multipart headers too large")
        header_name.extend(data[start:end])

    def header_data(data, start, end):
        nonlocal header_bytes
        header_bytes += end - start
        if header_bytes > 16 * 1024:
            raise HTTPException(422, "Multipart headers too large")
        header_value.extend(data[start:end])

    def header_end():
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        nonlocal name, mime
        disposition, fields = parse_options_header(headers.get(b"content-disposition", b""))
        raw_name = fields.get(b"filename")
        if disposition != b"form-data" or fields.get(b"name") != b"file" or raw_name is None:
            raise HTTPException(422, "Exactly one file field is required")
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(422, "Filename must be UTF-8") from None
        raw_mime = headers.get(b"content-type", b"application/octet-stream").decode("ascii", "ignore")
        name, mime = file_metadata(name, raw_mime)

    def part_data(data, start, end):
        nonlocal size
        size += end - start
        if size > limit:
            raise HTTPException(413, "File exceeds the 25 MB (26214400 bytes) limit")
        output.write(data[start:end])
        digest.update(data[start:end])

    def end():
        nonlocal finished
        finished = True

    parser = MultipartParser(boundary, {
        "on_part_begin": part_begin, "on_header_field": header_field,
        "on_header_value": header_data, "on_header_end": header_end,
        "on_headers_finished": headers_finished, "on_part_data": part_data, "on_end": end,
    })
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit + 64 * 1024:
                raise HTTPException(413, "File exceeds the 25 MB (26214400 bytes) limit")
            parser.write(chunk)
        parser.finalize()
        if not finished or name is None or parts != 1:
            raise HTTPException(422, "Incomplete multipart file")
        output.close()
        storage.complete(file_id)
        return {"name": name, "size": size, "content_type": mime, "sha256": digest.hexdigest()}
    except MultipartParseError:
        output.close()
        storage.discard(file_id)
        raise HTTPException(422, "Malformed multipart file") from None
    except (HTTPException, OSError, RuntimeError):
        output.close()
        storage.discard(file_id)
        raise
    finally:
        output.close()
