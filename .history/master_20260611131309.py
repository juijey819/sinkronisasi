import socket
import json
import time
import uuid
from datetime import datetime
import argparse


class BerkeleyMaster:
    def __init__(self, host="0.0.0.0", port=12300, interval=10, timeout=3):
        self.host = host
        self.port = port
        self.interval = interval
        self.timeout = timeout
        self.master_offset = 0.0

        self.slaves = {}  # addr -> node_id

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)

    def logical_time(self):
        return time.time() + self.master_offset

    def format_time(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]

    def send_json(self, data, addr):
        self.sock.sendto(json.dumps(data).encode(), addr)

    def recv_json(self):
        data, addr = self.sock.recvfrom(4096)
        return json.loads(data.decode()), addr

    def listen_for_registrations(self, duration=3):
        end_time = time.time() + duration

        while time.time() < end_time:
            try:
                msg, addr = self.recv_json()

                if msg.get("type") == "REGISTER":
                    node_id = msg.get("node_id", str(addr))
                    self.slaves[addr] = node_id
                    print(f"Slave terdaftar: {node_id} dari {addr}")

            except socket.timeout:
                continue
            except Exception as e:
                print("Error menerima data:", e)

    def synchronize(self):
        if not self.slaves:
            print("Belum ada slave terdaftar.")
            return

        sync_id = str(uuid.uuid4())
        sent_time = {}
        responses = {}

        print("\n=== MULAI SINKRONISASI BERKELEY ===")
        print(f"Waktu master sekarang: {self.format_time(self.logical_time())}")

        # Kirim request waktu ke semua slave
        for addr, node_id in list(self.slaves.items()):
            t0 = self.logical_time()
            sent_time[addr] = t0

            request = {
                "type": "TIME_REQUEST",
                "sync_id": sync_id,
                "master_time": t0
            }

            self.send_json(request, addr)
            print(f"Request waktu dikirim ke {node_id}")

        # Terima response dari slave
        deadline = time.time() + self.timeout

        while time.time() < deadline and len(responses) < len(self.slaves):
            try:
                msg, addr = self.recv_json()

                if msg.get("type") == "REGISTER":
                    node_id = msg.get("node_id", str(addr))
                    self.slaves[addr] = node_id
                    continue

                if msg.get("type") == "TIME_RESPONSE" and msg.get("sync_id") == sync_id:
                    t1 = self.logical_time()
                    slave_time = msg["slave_time"]

                    # Estimasi waktu master saat slave mengirim response
                    master_estimated_time = (sent_time[addr] + t1) / 2

                    # Selisih waktu slave terhadap master
                    diff = slave_time - master_estimated_time

                    responses[addr] = {
                        "node_id": msg.get("node_id", str(addr)),
                        "diff": diff,
                        "slave_time": slave_time
                    }

            except socket.timeout:
                continue
            except Exception as e:
                print("Error saat sinkronisasi:", e)

        if not responses:
            print("Tidak ada slave yang merespons.")
            return

        # Berkeley: rata-rata offset termasuk master sebagai 0
        total_diff = sum(item["diff"] for item in responses.values())
        average_offset = total_diff / (len(responses) + 1)

        print("\nHasil perhitungan offset:")
        print(f"Offset rata-rata sistem: {average_offset:.3f} detik")

        # Master juga menyesuaikan logical clock-nya
        self.master_offset += average_offset

        # Kirim adjustment ke tiap slave
        for addr, item in responses.items():
            node_id = item["node_id"]
            diff = item["diff"]

            adjustment = average_offset - diff

            adjust_msg = {
                "type": "ADJUST",
                "sync_id": sync_id,
                "adjustment": adjustment
            }

            self.send_json(adjust_msg, addr)

            print(
                f"{node_id}: "
                f"diff={diff:.3f}s, "
                f"adjustment={adjustment:.3f}s"
            )

        print(f"Waktu master setelah sinkronisasi: {self.format_time(self.logical_time())}")
        print("=== SINKRONISASI SELESAI ===\n")

    def run(self):
        print(f"Master berjalan di {self.host}:{self.port}")
        print("Menunggu slave...\n")

        while True:
            self.listen_for_registrations(duration=2)
            self.synchronize()
            time.sleep(self.interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=12300)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=3)

    args = parser.parse_args()

    master = BerkeleyMaster(
        host=args.host,
        port=args.port,
        interval=args.interval,
        timeout=args.timeout
    )

    master.run()