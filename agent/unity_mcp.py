"""Loopback JSON-RPC client for MCP for Unity (spec §7). Ported from FarmTestAgent/tools/farmqa_unity_identity.py."""
import json
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unity MCP redirects are unsupported')


class UnityMcp:
    """One synchronous inspection session; no reconnection or ambiguous retries.

    Supports the JSON/SSE request responses used by the installed server, not
    arbitrary MCP transports, subscriptions, authentication or server requests.

    The endpoint is a required argument rather than FarmQA's hardcoded 9090: this Mac's plugin defaults to
    8080, and `mcp_address` is a per-slot configuration key (DEFAULTS, Task 3).
    """
    RESOURCES = ("mcpforunity://custom-tools", "mcpforunity://instances", "mcpforunity://project/info",
                 "mcpforunity://editor/state")

    def __init__(self, endpoint, timeout=30):
        url = urllib.parse.urlsplit(endpoint)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1'
                or url.username or url.password or url.query or url.fragment
                or url.path != '/mcp' or url.port is None):
            raise ValueError('Explicit loopback Unity MCP endpoint required')
        self.endpoint, self.session, self.sequence = endpoint, None, 0
        self.timeout = timeout
        self.initialized = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _rpc(self, method, params, notification=False):
        self.sequence += 1
        payload = {'jsonrpc':'2.0', 'method':method, 'params':params}
        if not notification: payload['id'] = self.sequence
        headers = {'Content-Type':'application/json', 'Accept':'application/json, text/event-stream'}
        if self.session: headers['Mcp-Session-Id'] = self.session
        if self.initialized: headers['MCP-Protocol-Version'] = '2024-11-05'
        request = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            self.session = response.headers.get('Mcp-Session-Id', self.session)
            raw = response.read(1024*1024+1)
            content_type = response.headers.get_content_type()
        if len(raw) > 1024*1024: raise ValueError('Unity MCP response exceeds bound')
        if notification: return None
        if content_type == 'text/event-stream':
            messages = [json.loads(line[5:].strip()) for line in raw.decode('utf-8').splitlines()
                        if line.startswith('data:')]
            matches = [message for message in messages if message.get('id') == self.sequence]
            if len(matches) != 1: raise ValueError('Missing or ambiguous MCP reply')
            reply = matches[0]
        elif content_type == 'application/json':
            reply = json.loads(raw)
        else:
            raise ValueError('Unsupported MCP response format')
        if reply.get('id') != self.sequence or 'error' in reply or 'result' not in reply:
            raise ValueError('Unity MCP request failed')
        return reply['result']

    def _start(self):
        if self.initialized: return
        result = self._rpc('initialize', {'protocolVersion':'2024-11-05','capabilities':{},
            'clientInfo':{'name':'farmbot-identity','version':'1'}})
        if result.get('protocolVersion') != '2024-11-05':
            raise ValueError('Unsupported MCP protocol version')
        self.initialized = True
        self._rpc('notifications/initialized', {}, notification=True)

    @staticmethod
    def _payload(result, key):
        if result.get('isError'): raise ValueError('Unity MCP tool failed')
        blocks = result.get(key, [])
        texts = [item['text'] for item in blocks if 'text' in item]
        if len(texts) != 1: raise ValueError('Ambiguous Unity payload')
        value = json.loads(texts[0])
        if not isinstance(value, dict) or value.get('success') is False:
            raise ValueError('Unity observation failed')
        return value.get('data', value)

    def read_resource(self, uri):
        # The allow-list is checked before the handshake, so an unlisted URI costs nothing on the wire.
        if uri not in self.RESOURCES: raise ValueError(f'Unsupported identity resource: {uri}')
        self._start()
        return self._payload(self._rpc('resources/read', {'uri':uri}), 'contents')

    def call_tool(self, name, arguments):
        self._start()
        return self._payload(self._rpc('tools/call', {'name':name, 'arguments':arguments}), 'content')

    def select_instance(self, instance):
        """HTTP selection is per MCP session (the Mcp-Session-Id header), so each worker pins its own slot.
        UNITY_MCP_DEFAULT_INSTANCE is a stdio-only variable and has no effect on this path."""
        listed = [entry.get("id") for entry in self.read_resource("mcpforunity://instances").get("instances", [])]
        if instance not in listed:
            raise ValueError(f"instance {instance} is not connected; connected: {listed}")
        return self.call_tool("set_active_instance", {"instance": instance})
