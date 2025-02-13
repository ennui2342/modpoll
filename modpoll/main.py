import logging
import re
import threading
import datetime
from datetime import timezone
import sys
import signal
import json

from modpoll.arg_parser import get_parser
from modpoll.mqtt_task import mqttc_setup, mqttc_close, mqttc_receive
from modpoll.modbus_task import modbus_setup, modbus_poll, modbus_publish, modbus_publish_diagnostics, modbus_export, modbus_close, modbus_write_coil, modbus_write_register

LOG_SIMPLE = "%(asctime)s | %(levelname).1s | %(name)s | %(message)s"
log = None
event_exit = threading.Event()


def _signal_handler(signal, frame):
    log.info(f'Exiting {sys.argv[0]}')
    event_exit.set()


def get_utc_time():
    dt = datetime.datetime.now(timezone.utc)
    utc_time = dt.replace(tzinfo=timezone.utc)
    return utc_time.timestamp()


def app(name="modpoll"):

    print("\nmodpoll - A New Command Line Tool for Modbus\n", flush=True)

    signal.signal(signal.SIGINT, _signal_handler)

    # parse args
    args = get_parser().parse_args()

    # get logger
    logging.basicConfig(level=args.loglevel, format=LOG_SIMPLE)
    global log
    log = logging.getLogger(__name__)

    # setup mqtt
    if args.mqtt_host:
        log.info(f"Setup MQTT connection to {args.mqtt_host}")
        if not mqttc_setup(args):
            log.error("fail to setup MQTT client")
            mqttc_close()
            exit(1)
    else:
        log.info("No MQTT host specified, skip MQTT setup.")

    # setup modbus
    if not modbus_setup(args, event_exit):
        log.error("fail to setup modbus client(master)")
        modbus_close()
        mqttc_close()
        exit(1)

    # main loop
    last_check = 0
    last_diag = 0
    while not event_exit.is_set():
        now = get_utc_time()
        # routine check
        if now > last_check + args.rate:
            if last_check == 0:
                elapsed = args.rate
            else:
                elapsed = round(now - last_check, 6)
            last_check = now
            log.info(f" ====== modpoll polling at rate:{args.rate}s, actual:{elapsed}s ======")
            modbus_poll()
            if event_exit.is_set():
                break
            if args.mqtt_host:
                if args.timestamp:
                    modbus_publish(timestamp=now)
                else:
                    modbus_publish()
            if args.export:
                if args.timestamp:
                    modbus_export(args.export, timestamp=now)
                else:
                    modbus_export(args.export)
        if args.diagnostics_rate > 0 and now > last_diag + args.diagnostics_rate:
            last_diag = now
            modbus_publish_diagnostics()
        if event_exit.is_set():
            break
        # Check if receive mqtt request
        topic, payload = mqttc_receive()
        if topic and payload:
            device_name = re.search(f"^{args.mqtt_topic_prefix}([^/\n]*)/set", topic).group(1)
            if device_name:
                try:
                    reg = json.loads(payload)
                    if "coil" == reg["object_type"]:
                        if modbus_write_coil(device_name, reg["address"], reg["value"]):
                            log.info(f"")
                    elif "holding_register" == reg["object_type"]:
                        modbus_write_register(device_name, reg["address"], reg["value"])
                except json.decoder.JSONDecodeError:
                    log.warning(f"Fail to parse json message: {payload}")
        if args.once:
            event_exit.set()
            break
    modbus_close()
    mqttc_close()


if __name__ == "__main__":
    app()
