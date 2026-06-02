# Physical Alert Devices

Nurby can drive real hardware on alert. A buzzer, a siren, a lamp, a
speaker. Each device is a small receiver that listens for a Nurby
webhook and does one physical thing. Pick a preset in the rule builder,
flash or run the matching script, and point a webhook action at it.

The catalog and scripts are served by the API.

- `GET /api/devices` list every preset.
- `GET /api/devices/{id}` one preset (hardware list, wiring, steps).
- `GET /api/devices/{id}/receiver` the raw receiver script to copy.

The scripts themselves live under `integrations/devices/receivers/`.

## How it works

1. Nurby fires a rule and POSTs the standard alert payload to your
   device's `/alert` endpoint (see docs/webhooks.md for the payload).
2. The device verifies the `X-Nurby-Signature` HMAC if you set a secret.
3. The device acts. Beep, switch a relay, speak, sound a siren.

Every receiver returns `2xx` fast and does the physical action without
blocking, so Nurby's delivery never times out.

## Presets

### ESP32 Buzzer Alarm

A pocket Wi-Fi alarm. Sounds a piezo buzzer.

- Hardware. ESP32 board, active piezo buzzer.
- Wiring. Buzzer + to GPIO 23, buzzer - to GND.
- Script. `esp32_buzzer_alarm.ino` (Arduino IDE).
- Set `WIFI_SSID`, `WIFI_PASS`, and `SHARED_SECRET`, flash, and read the
  printed IP from the Serial Monitor at 115200 baud.

### ESP8266 Relay Lights

Switches a lamp, strobe, or siren through a relay.

- Hardware. ESP8266 (Wemos D1 mini or NodeMCU), opto-isolated 5V relay.
- Wiring. Relay IN to GPIO 5 (D1), VCC to 5V, GND to GND, load through
  COM and NO.
- Script. `esp8266_relay_lights.ino`. Tune `HOLD_MS` for on-time.
- Mains voltage is dangerous. Use an enclosed relay module and proper
  wiring, or drive a low-voltage siren instead.

### Raspberry Pi Speaker

Announces the alert out loud or plays a sound file.

- Hardware. Any networked Pi plus a speaker.
- Script. `raspberry_pi_speaker.py`.
- Setup. `pip install flask`, `sudo apt install espeak-ng`, then
  `NURBY_DEVICE_SECRET=yoursecret python3 raspberry_pi_speaker.py`.
- Listens on port 8088. Set `NURBY_SOUND_FILE` to play a chime instead
  of speaking.

### Raspberry Pi Relay Alarm

Drives a GPIO relay to fire a 12V siren or strobe.

- Hardware. Any Pi with GPIO, 5V relay, 12V siren with its own supply.
- Wiring. Relay IN to GPIO 17 (pin 11), VCC to 5V (pin 2), GND to GND
  (pin 6), siren through COM and NO.
- Script. `raspberry_pi_relay_alarm.py`.
- Setup. `pip install flask gpiozero`, then
  `NURBY_DEVICE_SECRET=yoursecret python3 raspberry_pi_relay_alarm.py`.
- Listens on port 8089. Tune `NURBY_SIREN_SECONDS` for burst length.

## Connecting a device to a rule

1. Run or flash the receiver and note the device IP.
2. In Nurby, edit a rule and add a webhook action.
3. Pick the device preset. Nurby pre-fills the action with the right URL
   shape (`http://<ip>:<port>/alert`) and the standard payload.
4. Enter the device IP, and the same secret you set on the device.
5. Save and fire a test event. The device should react.

## Security

Always set a secret in production so the device only acts on signed
Nurby messages. On a trusted home LAN you can leave it unset to skip
verification. Keep these devices off the public internet. They listen on
plain HTTP and are meant for your local network.
