# Arduino-to-Python motor protocol

The selected **Goniometer_Master_Mega.ino** uses ASCII, newline-terminated commands at **115200 baud**, with a numeric request ID. It advertises `GONIO_MASTER_1`; this must match the Python controller.

| Example command | Typical response type |
|---|---|
| `PING,1` | `OK,1,PING,GONIO_MASTER_1` |
| `STATUS,2` | `STATUS,2,...` |
| `ZERO,3` | `OK,3,ZERO,0` |
| `MOVE,10,4` | `ACK,4,...` then `DONE,4,...` |
| `JOG,-1,5` | `ACK,5,...` then `DONE,5,...` |
| `STOP,6` | `OK,6,STOP,...` and potentially `ABORT` for active motion |
| `SPEED,350,7` | `OK,7,SPEED,...` |
| `ACCEL,400,8` | `OK,8,ACCEL,...` |

See the firmware header for the complete message vocabulary and exact fields. Request IDs are used for matching asynchronous commands and completion responses. `DONE` indicates that the driver pulses were generated; **not** that a shaft encoder confirmed position. Motion timeout and STOP require independent physical checks, and a re-zero may be necessary.

**Do not substitute** `WildWest_Mega_v5.ino` (the separate 9600-baud `HELLO`/`GOTO_DEG` protocol), as it is not compatible with this V10 controller.

---

*Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠*
