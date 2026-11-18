"""按内容版本缓存前端资源；相对模块导入共享同一版本，不缓存个人页面。"""
from hashlib import sha256
from io import BytesIO

from flask import abort, send_file, url_for


def register_web_assets(app):
    from pathlib import Path
    import mimetypes
    # Windows 的系统 MIME 表不一定登记 WebP；静态教学图和头像统一返回正确类型。
    mimetypes.add_type('image/webp', '.webp')
    directory = Path(app.static_folder).resolve()
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob('*'))
        if path.is_file() and (path.suffix in {'.js', '.css', '.svg'} or
                              path.parent.name=='avatars' and path.suffix=='.webp')
    }
    digest = sha256()
    for name, content in files.items():
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(sha256(content).digest())
    version = digest.hexdigest()[:20]

    def asset_url(filename):
        if filename not in files:
            raise ValueError('前端资源未登记：' + filename)
        return url_for('versioned_static', version=version, filename=filename)

    @app.get('/static/bundle/<version>/<path:filename>')
    def versioned_static(version, filename):
        # 旧内容地址不能返回新文件，避免把修改后的代码缓存到旧身份下。
        if version != app.extensions['web_assets']['version'] or filename not in files:
            abort(404)
        content = files[filename]
        response = send_file(BytesIO(content), download_name=filename,
                             etag=sha256(content).hexdigest(), conditional=True, max_age=31536000)
        response.cache_control.immutable = True
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    app.extensions['web_assets'] = {'version': version, 'files': files}
    app.jinja_env.globals['asset_url'] = asset_url
    app.jinja_env.globals['asset_base'] = '/static/bundle/' + version
