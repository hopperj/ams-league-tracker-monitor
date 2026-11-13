import argparse
import logging

from .configuration import defaults, prepare_configuration
from .engine import DEFAULT_BATCH_SECONDS, run
from .spool import Spool
from .transport import Transport
from .status import receipt_notice


def configure(args):
    prepare_configuration(args.config, args.spool, args.server, args.event)
    print("Recorder setup saved. Open the recorder window and use Link Steam account before recording.")


def main():
    config, spool = defaults()
    parser = argparse.ArgumentParser(description="AMS2 recorder: full shared-memory snapshots and retry-safe batched uploads")
    parser.add_argument("--config", default=str(config))
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--server", help="League server address; defaults to the saved server or ams2.ioresearch.ca")
    setup.add_argument("--spool", default=str(spool))
    setup.add_argument("--event", help="AUTO records all driving sessions; an explicit shared capture code remains supported")
    gui = sub.add_parser("gui", help="Open the recorder window")
    gui.add_argument("--spool", default=str(spool))
    gui.add_argument("--demo", action="store_true")
    gui.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    record = sub.add_parser("run")
    record.add_argument("--spool", default=str(spool))
    record.add_argument("--hz", type=float, default=60)
    record.add_argument("--batch-seconds", type=float, default=DEFAULT_BATCH_SECONDS)
    record.add_argument("--max-spool-mb", type=int, default=2048)
    record.add_argument("--demo", action="store_true")
    record.add_argument("--demo-offset", type=float, default=0)
    record.add_argument("--duration", type=float)
    record.add_argument("--replay", help="Replay a downloaded raw batch; excluded from league scoring")
    record.add_argument("--udp", action="store_true", help="Also archive raw pCARS2-family UDP datagrams")
    record.add_argument("--no-ai-detection", dest="udp_detection", action="store_false",
                        help="Disable human/AI detection from the game's UDP feed")
    record.add_argument("--udp-port", type=int, default=5606)
    record.add_argument("--udp-source", help="Optional game PC IPv4 address filter for UDP")
    send = sub.add_parser("flush", help="Upload all queued batches without starting capture")
    send.add_argument("--spool", default=str(spool))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "configure":
            configure(args)
        elif args.command == "gui":
            from .gui import main as open_window
            options = ["--config",args.config,"--spool",args.spool]
            if args.demo:
                options.append("--demo")
            if args.smoke_test:
                options.append("--smoke-test")
            open_window(options)
        elif args.command == "flush":
            config = prepare_configuration(args.config, args.spool)
            transport = Transport(config["server"], config["token"])
            outgoing = Spool(args.spool)
            try:
                outgoing.bind(transport.server, config["recorder_id"])
                if config.get('auto_registration') and outgoing.pending():
                    transport.register(config['recorder_id'])
                if outgoing.pending():
                    transport.health(config['recorder_id'])
                while outgoing.pending():
                    batch_id, payload = outgoing.batch(config["recorder_id"])
                    ack = transport.request("/api/batches", payload)
                    if (ack.get("batch_id") != batch_id or ack.get("accepted") is not True
                            or ("stored" in ack and ack["stored"] is not True)):
                        raise ValueError("Server did not acknowledge this batch; queue retained")
                    outgoing.acknowledge(batch_id)
                    notice=receipt_notice(ack)
                    if notice:
                        logging.warning("%s",notice.replace(config["token"],"[redacted]"))
                print("All queued snapshots uploaded.")
            finally:
                outgoing.close()
        else:
            if not 1 <= args.hz <= 240 or not .1 <= args.batch_seconds <= 60 or args.max_spool_mb < 1:
                parser.error("Use 1–240 Hz, 0.1–60 second batches, and a positive spool limit")
            if args.demo and args.replay:
                parser.error("Choose --demo or --replay")
            if args.udp and (args.demo or args.replay):
                parser.error("Use UDP archiving with live capture, separately from demo/replay")
            if not 1 <= args.udp_port <= 65535:
                parser.error("Invalid UDP port")
            run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, "%s\n" % exc)


if __name__ == "__main__":
    main()
