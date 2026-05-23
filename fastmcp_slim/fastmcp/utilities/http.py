import socket


def find_available_port(host: str = "127.0.0.1") -> int:
    """Find an available port by letting the OS assign one."""
    addr_info = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM)[0]
    family, socket_type, protocol, _, socket_address = addr_info
    with socket.socket(family, socket_type, protocol) as s:
        s.bind(socket_address)
        return s.getsockname()[1]
