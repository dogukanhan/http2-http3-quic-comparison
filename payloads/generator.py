import json
import random
import string
import argparse

def random_string(length):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def generate_json(target_size):
    data = {
        "meta": {
            "request_id": random_string(12),
            "timestamp": random.randint(1600000000, 1800000000)
        },
        "data": []
    }

    # yaklaşık büyütmek için sürekli eleman ekle
    while True:
        item = {
            "id": random.randint(1, 1000000),
            "name": random_string(10),
            "email": random_string(5) + "@example.com",
            "value": random.random(),
            "tags": [random_string(5) for _ in range(3)]
        }

        data["data"].append(item)

        json_str = json.dumps(data)
        size = len(json_str.encode("utf-8"))

        if size >= target_size:
            return json_str, size


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, required=True, help="Target size in bytes")
    parser.add_argument("--output", type=str, default=None, help="Output file (optional)")
    args = parser.parse_args()

    json_str, actual_size = generate_json(args.size)

    print(f"Generated JSON size: {actual_size} bytes")

    if args.output:
        with open(args.output, "w") as f:
            f.write(json_str)
    else:
        print(json_str)