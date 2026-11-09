# This is code written by Gemini for a simple espnow test "ping-pong" sending and receiving test. Mac Addresses
# are kind of hardcoded, GUI section has correct sensor node mac while the sensor section doesn't have the correct
# GUI Mac address, its just a place holder.


"""Sensor Node"""
import time
import wifi
import espnow

# Target GUI Hub MAC Address
GUI_HUB_MAC = b"\x24\x6f\x28\x11\x22\x33"

wifi.radio.enabled = True
e = espnow.ESPNow()

# Fixed Peer definition:
peer = espnow.Peer(mac=GUI_HUB_MAC)
e.peers.append(peer)

print("Sensor Node active. Waiting for PING requests...")

while True:
    if e:
        packet = e.read()
        if packet:
            msg = packet.msg.decode("utf-8")
            print(f"Received: {msg}")
            if msg.startswith("PING"):
                reply = msg.replace("PING", "PONG").encode("utf-8")
                e.send(reply, packet.mac)
                print(f"Sent reply: {reply.decode('utf-8')}")
    time.sleep(0.01)



"""UI HUB"""
# import time
# import wifi
# import espnow

# SunFounder MAC Address from Web Flasher log
SENSOR_NODE_MAC = b"\x5c\x01\x3b\x9b\xb3\x58"

wifi.radio.enabled = True
e = espnow.ESPNow()

# Fixed Peer definition:
peer = espnow.Peer(mac=SENSOR_NODE_MAC)
e.peers.append(peer)

seq = 0
print("GUI Hub starting ESP-NOW Ping test...")

while True:
    seq += 1
    msg = f"PING:{seq}".encode("utf-8")
    print(f"Sending: {msg.decode('utf-8')}")
    e.send(msg, e.peers[0])

    start = time.monotonic()
    got_pong = False
    while time.monotonic() - start < 0.5:
        if e:
            packet = e.read()
            if packet:
                print(f"Response Received: {packet.msg.decode('utf-8')}")
                got_pong = True
                break
        time.sleep(0.01)

    if not got_pong:
        print("No response from Sensor Node (Timeout)")

    time.sleep(2.0)