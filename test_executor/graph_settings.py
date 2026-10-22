"""Labels and output folders for each benchmark sweep's fixed settings."""


def graph_settings(key: tuple, x_axis: str) -> tuple[str, str, str]:
    if x_axis == "delay":
        case, requests, payload, clients, streams, loss = key
        label = "Delay (ms)"
        fixed = f"{payload} KB payload  ·  {clients} clients  ·  {streams} streams"
        folder = f"n{requests}-p{payload}-c{clients}-m{streams}-loss{loss}"
    elif x_axis in ("payload", "clients"):
        case, requests, value, streams, delay, loss = key
        label = "Number of clients" if x_axis == "clients" else "Payload (KB)"
        fixed = f"{value} KB payload" if x_axis == "clients" else f"{value} clients"
        fixed += f"  ·  {streams} streams  ·  {delay} ms delay"
        prefix = "p" if x_axis == "clients" else "c"
        folder = f"n{requests}-{prefix}{value}-m{streams}-delay{delay}-loss{loss}"
    else:
        raise ValueError("x_axis must be payload, clients, or delay")
    return label, f"{case}  ·  {requests:,} requests  ·  {fixed}  ·  {loss}% loss", folder
