import socket
import sys

if __name__ == "__main__":
    print(socket.gethostbyname(sys.argv[1]))
