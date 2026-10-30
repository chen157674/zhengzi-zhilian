"""可选手机HTTPS入口；后端保持回环监听，只信任本机反向代理的明确来源。"""
import ipaddress,json,re
from urllib.parse import urlsplit
from flask import request
from flask.sessions import SecureCookieSessionInterface


def public_origin(value):
    if not value:return None
    if not isinstance(value,str) or len(value)>300:raise ValueError('手机访问地址无效')
    try:
        parsed=urlsplit(value);host=parsed.hostname;port=parsed.port
        if parsed.scheme!='https' or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('origin')
        if ':' in host:
            ipaddress.IPv6Address(host);authority='['+host+']'
        else:
            host=host.encode('idna').decode('ascii').lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '..' in host:raise ValueError('hostname')
            authority=host
        if port not in (None,443):authority+=':'+str(port)
        return 'https://'+authority
    except (ValueError,UnicodeError):raise ValueError('手机访问地址必须是明确的HTTPS来源，不含路径、通配符或账号') from None


def _loopback_peer(value):
    if not isinstance(value,str) or not value:return False
    candidate=value.strip()
    try:return ipaddress.ip_address(candidate).is_loopback
    except ValueError:pass
    host=port=None
    if candidate.startswith('['):
        end=candidate.find(']')
        if end>0 and candidate[end+1:].startswith(':'):host,port=candidate[1:end],candidate[end+2:]
    elif candidate.count(':')==1:
        host,port=candidate.rsplit(':',1)
    if not host or not port or not port.isdigit() or not 1<=int(port)<=65535:return False
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return False


class OriginProxy:
    def __init__(self,app,origin):self.app=app;self.origin=origin;self.authority=urlsplit(origin).netloc
    def __call__(self,environ,start_response):
        host=environ.get('HTTP_HOST','').lower()
        # 不信任X-Forwarded-Host，不开放任意Host；只提升明确入口的本机TLS代理请求。
        if host in {self.authority,self.authority+':443' if ':' not in self.authority else self.authority}:
            peer=environ.get('REMOTE_ADDR');proto=environ.get('HTTP_X_FORWARDED_PROTO')
            secure=proto=='https' or proto is None and environ.get('wsgi.url_scheme')=='https'
            if not _loopback_peer(peer) or not secure or environ.get('PATH_INFO','').startswith('/_local/'):
                raw=json.dumps({'error':{'code':'SECURE_ORIGIN_REQUIRED','message':'请通过已配置的HTTPS入口访问','fields':{},'retryable':False}},ensure_ascii=False).encode()
                start_response('403 Forbidden',[('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(raw))),('Cache-Control','no-store')]);return [raw]
            environ['wsgi.url_scheme']='https';environ['HTTP_HOST']=self.authority
        return self.app(environ,start_response)


class SchemeCookies(SecureCookieSessionInterface):
    def get_cookie_secure(self,app):return bool(app.config['SESSION_COOKIE_SECURE'] or request.is_secure)


def configure_mobile_origin(app,value):
    origin=public_origin(value)
    if not origin:return
    parsed=urlsplit(origin);trusted=parsed.hostname
    if ':' in trusted:trusted='['+trusted+']'
    if trusted in {'localhost','127.0.0.1','[::1]'}:raise ValueError('手机访问地址不能是电脑自身的回环地址')
    app.config['MOBILE_PUBLIC_ORIGIN']=origin
    app.config['TRUSTED_HOSTS']=[*app.config['TRUSTED_HOSTS'],trusted]
    app.session_interface=SchemeCookies();app.wsgi_app=OriginProxy(app.wsgi_app,origin)
