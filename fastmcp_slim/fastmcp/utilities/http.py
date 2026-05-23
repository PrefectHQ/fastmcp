import socket


def find_available_port(host: str = "127.0.0.1") -> int:
    """Find an available port by letting the OS assign one."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
