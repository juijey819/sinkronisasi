import socket
import json
import time
from datetime import datetime
import argparse
import random


class BerkeleySlave:
    def __init__(self, master_host, master_port=12300, node_id=None, initial_offset=None):
        self.master_addr = (master_host, master_port)

        # Kalau node_id tidak diisi, otomatis dibuat random.
        self.node_id = node_id or f"slave-{random.randint(1000, 9999)}"

        # Kalau offset tidak diisi, otomatis dibuat random agar demo terlihat beda waktu.
        self.offset = initial_offset if initial_offset is not None else random.uniform(-3600, 3600)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Bind port random agar banyak slave bisa jalan di satu laptop.
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

    def run(self):
        print(f"{self.node_id} berjalan.")
        print(f"Master                  : {self.master_addr}")
        print(f"Jam Windows slave awal  : {self.format_time(time.time())}")
        print(f"Offset awal slave       : {self.offset:.6f} detik")
        print(f"Jam Berkeley slave awal : {self.format_time(self.logical_time())}\n")

        last_register = 0

        while True:
            # Register berkala ke master.
            if time.monotonic() - last_register > 5:
                self.register_to_master()
                last_register = time.monotonic()
                print(f"Register dikirim ke master {self.master_addr}")

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

                    print("\nTIME_REQUEST diterima dari master")
                    print(f"Jam Windows slave saat ini  : {self.format_time(time.time())}")
                    print(f"Jam Berkeley slave dikirim  : {self.format_time(slave_time)}")
                    print(f"Offset slave saat ini       : {self.offset:.6f} detik")

                elif msg.get("type") == "ADJUST":
                    adjustment = msg["adjustment"]

                    before_offset = self.offset
                    before_logical = self.logical_time()

                    # Slave menyesuaikan logical clock berdasarkan adjustment dari master.
                    self.offset += adjustment

                    after_offset = self.offset
                    after_logical = self.logical_time()

                    print("\n=== ADJUSTMENT DITERIMA DARI MASTER ===")
                    print(f"Adjustment diterima      : {adjustment:.6f} detik")
                    print(f"Offset sebelum           : {before_offset:.6f} detik")
                    print(f"Offset sesudah           : {after_offset:.6f} detik")
                    print(f"Jam Berkeley sebelum     : {self.format_time(before_logical)}")
                    print(f"Jam Berkeley sesudah     : {self.format_time(after_logical)}")
                    print(f"Jam Windows slave tetap  : {self.format_time(time.time())}")
                    print("=======================================\n")

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

    slave = BerkeleySlave(
        master_host=args.master,
        master_port=args.port,
        node_id=args.node_id,
        initial_offset=args.offset
    )

    slave.run()