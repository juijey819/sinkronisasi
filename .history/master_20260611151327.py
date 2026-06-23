import socket
import json
import time
import uuid
from datetime import datetime
import argparse
import platform
import ctypes
from ctypes import wintypes


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", wintypes.WORD),
        ("wMonth", wintypes.WORD),
        ("wDayOfWeek", wintypes.WORD),
        ("wDay", wintypes.WORD),
        ("wHour", wintypes.WORD),
        ("wMinute", wintypes.WORD),
        ("wSecond", wintypes.WORD),
        ("wMilliseconds", wintypes.WORD),
    ]


class BerkeleyMaster:
    def __init__(
        self,
        host="0.0.0.0",
        port=12300,
        interval=10,
        timeout=3,
        set_system_clock=False
    ):
        self.host = host
        self.port = port
        self.interval = interval
        self.timeout = timeout
        self.set_system_clock = set_system_clock

        # Kalau jam Windows tidak diubah, offset ini dipakai sebagai logical clock.
        self.master_offset = 0.0

        # Menyimpan daftar slave.
        # Format: {alamat_slave: node_id}
        self.slaves = {}

        # Membuat socket UDP.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(0.5)

    def logical_time(self):
        """
        Waktu Berkeley milik master.
        Kalau set_system_clock aktif, master_offset biasanya 0
        karena jam Windows asli yang diubah.
        """
        return time.time() + self.master_offset

    def format_time(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]

    def send_json(self, data, addr):
        self.sock.sendto(json.dumps(data).encode(), addr)

    def recv_json(self):
        data, addr = self.sock.recvfrom(4096)
        return json.loads(data.decode()), addr

    def set_windows_time(self, target_timestamp):
        """
        Mengubah jam Windows asli.
        Wajib menjalankan terminal sebagai Administrator.
        """
        if platform.system() != "Windows":
            raise RuntimeError("Fitur ubah jam sistem hanya tersedia untuk Windows.")

        dt = datetime.fromtimestamp(target_timestamp)

        # Windows: Sunday = 0, Monday = 1, ..., Saturday = 6
        day_of_week = (dt.weekday() + 1) % 7

        system_time = SYSTEMTIME(
            dt.year,
            dt.month,
            day_of_week,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second,
            int(dt.microsecond / 1000),
        )

        result = ctypes.windll.kernel32.SetLocalTime(ctypes.byref(system_time))

        if result == 0:
            raise ctypes.WinError()

    def listen_for_registrations(self, duration=3):
        """
        Menunggu slave melakukan REGISTER.
        Menggunakan time.monotonic agar aman walaupun jam Windows berubah.
        """
        end_time = time.monotonic() + duration

        while time.monotonic() < end_time:
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
        print(f"Jam Windows master sebelum  : {self.format_time(time.time())}")
        print(f"Jam Berkeley master sebelum : {self.format_time(self.logical_time())}")
        print(f"Jumlah slave terdaftar      : {len(self.slaves)}")

        # 1. Master mengirim request waktu ke semua slave
        for addr, node_id in list(self.slaves.items()):
            t0 = self.logical_time()
            sent_time[addr] = t0

            request = {
                "type": "TIME_REQUEST",
                "sync_id": sync_id,
                "master_time": t0
            }

            self.send_json(request, addr)
            print(f"Request waktu dikirim ke {node_id} {addr}")

        # 2. Master menerima response dari slave
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline and len(responses) < len(self.slaves):
            try:
                msg, addr = self.recv_json()

                if msg.get("type") == "REGISTER":
                    node_id = msg.get("node_id", str(addr))
                    self.slaves[addr] = node_id
                    continue

                if msg.get("type") == "TIME_RESPONSE" and msg.get("sync_id") == sync_id:
                    t1 = self.logical_time()
                    slave_time = msg["slave_time"]
                    t0 = sent_time[addr]

                    # Round trip delay = waktu response diterima - waktu request dikirim
                    round_trip_delay = t1 - t0

                    # Estimasi waktu master ketika slave membalas
                    master_estimated_time = (t0 + t1) / 2

                    # Offset slave terhadap master
                    # Jika positif: slave lebih cepat
                    # Jika negatif: slave lebih lambat
                    diff = slave_time - master_estimated_time

                    responses[addr] = {
                        "node_id": msg.get("node_id", str(addr)),
                        "t0": t0,
                        "t1": t1,
                        "round_trip_delay": round_trip_delay,
                        "slave_time": slave_time,
                        "master_estimated_time": master_estimated_time,
                        "diff": diff
                    }

            except socket.timeout:
                continue
            except Exception as e:
                print("Error saat sinkronisasi:", e)

        if not responses:
            print("Tidak ada slave yang merespons.")
            return

        # 3. Hitung rata-rata offset Berkeley
        total_diff = sum(item["diff"] for item in responses.values())
        jumlah_slave = len(responses)
        jumlah_node = jumlah_slave + 1  # +1 karena master ikut dihitung dengan offset 0
        average_offset = total_diff / jumlah_node

        print("\n=== DETAIL PERHITUNGAN OFFSET TIAP SLAVE ===")

        for i, (addr, item) in enumerate(responses.items(), start=1):
            node_id = item["node_id"]

            print(f"\nSlave {i} ({node_id})")
            print(f"Alamat slave              : {addr}")
            print(f"t0 master kirim request   : {self.format_time(item['t0'])}")
            print(f"t1 master terima response : {self.format_time(item['t1'])}")
            print(f"Round trip delay          : {item['round_trip_delay']:.6f} detik")
            print(f"Waktu slave               : {self.format_time(item['slave_time'])}")
            print(f"Estimasi waktu master     : {self.format_time(item['master_estimated_time'])}")
            print(
                f"Diff slave                : "
                f"{item['diff']:.6f} detik "
                f"(slave_time - master_estimated_time)"
            )

        print("\n=== DETAIL RATA-RATA BERKELEY ===")
        print("Offset master             : 0.000000 detik")
        print(f"Total diff semua slave    : {total_diff:.6f} detik")
        print(f"Jumlah slave merespons    : {jumlah_slave}")
        print(f"Jumlah node total         : {jumlah_node} node")
        print(
            f"Average offset            : "
            f"{total_diff:.6f} / {jumlah_node} = {average_offset:.6f} detik"
        )

        # 4. Master ikut disesuaikan
        print("\n=== PENYESUAIAN MASTER ===")

        if self.set_system_clock:
            try:
                before_windows = time.time()
                target_windows = before_windows + average_offset

                print("Mode master               : ubah jam Windows asli")
                print(f"Jam Windows sebelum       : {self.format_time(before_windows)}")
                print(f"Koreksi master            : {average_offset:.6f} detik")
                print(f"Target jam Windows        : {self.format_time(target_windows)}")

                self.set_windows_time(target_windows)

                # Karena jam Windows asli sudah berubah,
                # master_offset harus direset agar tidak dobel koreksi.
                self.master_offset = 0.0

                time.sleep(0.2)

                print(f"Jam Windows sesudah       : {self.format_time(time.time())}")

            except Exception as e:
                print("Gagal mengubah jam Windows master.")
                print("Kemungkinan terminal belum Run as Administrator.")
                print("Fallback: logical clock master saja yang diubah.")
                print("Error:", e)

                self.master_offset += average_offset

        else:
            print("Mode master               : logical clock saja")
            print(f"Logical offset sebelum    : {self.master_offset:.6f} detik")
            self.master_offset += average_offset
            print(f"Koreksi master            : {average_offset:.6f} detik")
            print(f"Logical offset sesudah    : {self.master_offset:.6f} detik")

        # 5. Kirim adjustment ke setiap slave
        print("\n=== DETAIL ADJUSTMENT UNTUK SETIAP SLAVE ===")

        for addr, item in responses.items():
            node_id = item["node_id"]
            diff = item["diff"]

            # Rumus Berkeley untuk slave:
            # adjustment = average_offset - diff_slave
            adjustment = average_offset - diff

            adjust_msg = {
                "type": "ADJUST",
                "sync_id": sync_id,
                "adjustment": adjustment
            }

            self.send_json(adjust_msg, addr)

            print(f"\nAdjustment untuk {node_id} {addr}")
            print("Rumus                    : average_offset - diff_slave")
            print(
                f"Perhitungan              : "
                f"{average_offset:.6f} - ({diff:.6f})"
            )
            print(f"Adjustment dikirim       : {adjustment:.6f} detik")

        print("\n=== HASIL AKHIR MASTER ===")
        print(f"Jam Windows master akhir  : {self.format_time(time.time())}")
        print(f"Jam Berkeley master akhir : {self.format_time(self.logical_time())}")
        print("=== SINKRONISASI SELESAI ===\n")

    def run(self):
        print(f"Master berjalan di {self.host}:{self.port}")

        if self.set_system_clock:
            print("Mode: Berkeley asli + jam Windows master ikut berubah")
            print("Pastikan terminal dijalankan sebagai Administrator.")
        else:
            print("Mode: Berkeley asli + logical clock master saja")

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

    parser.add_argument(
        "--set-system-clock",
        action="store_true",
        help="Mengubah jam Windows master sesuai hasil Berkeley Algorithm"
    )

    args = parser.parse_args()

    master = BerkeleyMaster(
        host=args.host,
        port=args.port,
        interval=args.interval,
        timeout=args.timeout,
        set_system_clock=args.set_system_clock
    )

    master.run()