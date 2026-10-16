"""Inspect a scoped stored model for a network-free authenticated draft preview."""
import hashlib
import json
import re
import struct

from .storage_security import resolve_storage_asset_path, StorageSecurityError

MAX_MODEL_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
CODEC_EXTENSIONS = {'KHR_draco_mesh_compression', 'KHR_texture_basisu', 'EXT_meshopt_compression'}
EMBEDDED_URI = re.compile(r'^data:(?:application/(?:octet-stream|gltf-buffer)|image/(?:png|jpeg|webp));base64,[A-Za-z0-9+/=\s]*$')


class ModelPreviewError(ValueError):
    pass


def inspect_model(asset):
    if asset.asset_type != 'model3d' or asset.file_ext.lower() not in {'.glb', '.gltf'}:
        raise ModelPreviewError('该格式尚无本地模型预览，请使用原资产下载入口核对文件。')
    try:
        source = resolve_storage_asset_path(asset.storage_path)
        size = source.stat().st_size
        if size > MAX_MODEL_BYTES:
            raise ModelPreviewError('模型超过本地预览大小限制；原文件未更改。')
        with source.open('rb') as stream:
            if size != asset.file_size_bytes or hashlib.file_digest(stream, 'sha256').hexdigest() != asset.sha256:
                raise ValueError('asset changed')
            stream.seek(0)
            if asset.file_ext.lower() == '.glb':
                magic, version, length = struct.unpack('<4sII', stream.read(12))
                json_length, kind = struct.unpack('<I4s', stream.read(8))
                if magic != b'glTF' or version != 2 or length != size or kind != b'JSON' or 20+json_length > size:
                    raise ValueError('invalid GLB')
            else:
                json_length = size
            if json_length > MAX_JSON_BYTES:
                raise ModelPreviewError('模型描述超过本地预览大小限制；原文件未更改。')
            document = json.loads(stream.read(json_length).decode('utf-8'))
        if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
            raise ValueError('invalid glTF')
        todo = [document]
        count = 0
        while todo:
            value = todo.pop()
            count += 1
            if count > 100_000:
                raise ModelPreviewError('模型描述过于复杂，无法安全预览；原文件未更改。')
            if isinstance(value, dict):
                if 'uri' in value and (not isinstance(value['uri'], str) or not EMBEDDED_URI.fullmatch(value['uri'])):
                    raise ModelPreviewError('模型依赖外部资源，预览不会请求这些地址。请导出包含资源的独立 GLB。')
                if CODEC_EXTENSIONS.intersection(value) or any(
                        extension in CODEC_EXTENSIONS for extension in value.get('extensionsUsed', [])
                        if isinstance(extension, str)):
                    raise ModelPreviewError('模型需要未安装的解码器，预览不会连接外部下载。请使用未压缩的独立 GLB。')
                todo.extend(value.values())
            elif isinstance(value, list):
                todo.extend(value)
    except ModelPreviewError:
        raise
    except (OSError, StorageSecurityError, ValueError, TypeError, AttributeError, struct.error, RecursionError):
        raise ModelPreviewError('模型文件不可用或已变化；请返回资产库核对原文件。') from None
