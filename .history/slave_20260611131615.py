import socket
import json
import time
from datetime import datetime
import argparse
import random


class BerkeleySlave:
    def __init__(self, master_host, master_port=12300, node_id="slave", initial_offset=0):
        self.master_addr = (master_host, master_port)
        self.node_id = node_id
        self.offset = initial_offset

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Bind port random agar bisa jalan banyak slave di satu laptop
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(1)

    def logical_time(self):
        return time.time() + self.offset

    def format_time(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]

    def send_json(self, data, addr):
        self.sock.sendto(json.dumps(data).encode(), addr)

    def recv_json(self):
        data, addr = self.sock.recvfrom(4096)
        return json.loads(data.decode()), addr

    def register_to_master(self):
        msg = {
            "type": "REGISTER",
            "node_id": self.node_id
        }

        self.send_json(msg, self.master_addr)
        print(f"{self.node_id} register ke master {self.master_addr}")

    def run(self):
        print(f"{self.node_id} berjalan.")
        print(f"Initial offset: {self.offset:.3f} detik")
        print(f"Waktu logical awal: {self.format_time(self.logical_time())}\n")

        last_register = 0

        while True:
            # Kirim register berkala agar master tahu slave ini aktif
            if time.time() - last_register > 5:
                self.register_to_master()
                last_register = time.time()

            try:
                msg, addr = self.recv_json()

                if msg.get("type") == "TIME_REQUEST":
                    sync_id = msg["sync_id"]

                    slave_time = self.logical_time()

                    response = {
                        "type": "TIME_RESPONSE",
                        "sync_id": sync_id,
                        "node_id": self.node_id,
                        "slave_time": slave_time
                    }

                    self.send_json(response, self.master_addr)

                    print(
                        f"Request diterima dari master. "
                        f"Waktu slave: {self.format_time(slave_time)}"
                    )

                elif msg.get("type") == "ADJUST":
                    adjustment = msg["adjustment"]

                    before = self.logical_time()
                    self.offset += adjustment
                    after = self.logical_time()

                    print("\nAdjustment diterima dari master")
                    print(f"Koreksi waktu: {adjustment:.3f} detik")
                    print(f"Sebelum: {self.format_time(before)}")
                    print(f"Sesudah: {self.format_time(after)}\n")

            except socket.timeout:
                continue
            except Exception as e:
                print("Error:", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", required=True)
    parser.add_argument("--port", type=int, default=12300)
    parser.add_argument("--node-id", default=None)
    parser.add_argument("--offset", type=float, default=None)

    args = parser.parse_args()

    node_id = args.node_id or f"slave-{random.randint(1000, 9999)}"

    # Kalau offset tidak diisi, dibuat random agar terlihat beda waktunya
    initial_offset = args.offset if args.offset is not None else random.uniform(-120, 120)

    slave = BerkeleySlave(
        master_host=args.master,
        master_port=args.port,
        node_id=node_id,
        initial_offset=initial_offset
    )

    slave.run()