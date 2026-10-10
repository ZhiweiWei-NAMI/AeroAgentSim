# Current P09 radio checkpoint

This is the v5 P09 native receipt replay, not BENCH public.network.link.v3. No new simulation was executed.

| Parameter | Current value | Evidence |
|---|---|---|
| PHY / MAC / station manager | YansWifiPhy / AdhocWifiMac / ConstantRateWifiManager, 802.11n | Extension source; loaded standard |
| Channel / frequency / width | Channel1 /2412MHz /20MHz | Completed run loaded-radio export |
| Data / control mode | HtMcs0 / HtMcs0 | Loaded export; nominal1SS long-GI rate6.5Mbps, not measured goodput |
| Power / scalar gains / noise figure |16dBm /0dB Tx and Rx /7dB NF | Applied configuration and loaded export; NF has no public getter |
| RxSensitivity |-95dBm | Loaded export; not a complete PER curve |
| Loss | LogDistance, n=3,d0=1m,L0=40.09532929124565dB | Loaded model/attributes; R1 frequency correction |
| Delay | ConstantSpeedPropagationDelayModel | Explicit local source; exact3.48 source speed default299792458m/s |
| Error/interference | TableBasedErrorRateModel, YansErrorRateModel fallback; InterferenceHelper | Exact3.48 helper defaults, no local override; not emitted as runtime getters |
| MAC attempt limit | WifiMac::FrameRetryLimit=7 total transmission attempts | Exact3.48 TypeId default, no local override; deprecated MaxSsrc/MaxSlrc not used |
| MAC queues |4 EDCA queues per node; each500packets /500ms | Actual loaded queues |
| UDP payload / telemetry load |256B /100ms =20.48kbps per source | Actual application configuration |
| Controlled fault load |1200B /1.2ms =8Mbps,40..52s | Declared offered load, not channel bandwidth or throughput |
| TTL / mature window |200ms /1s | Observation contract; strict RX<firstTX+TTL |
| Commands |64B,3attempts spaced100ms;2s command deadline | Application command policy; distinct from MAC retry and packetTTL |

There is one shared YansWifiChannel for the whole NodeContainer. Links are not isolated. Legitimate cochannel flows already compete through real MAC/channel reception mechanics. R1 has no building obstruction, shadowing, added fast fading or rain attenuation.

Numeric RSSI/SNR/SINR callbacks are not connected and these are not prediction-state fields. Receiver propagation/noise/interference affect real packet reception; current P01 directly consumes only L/P. Derived integrated noise is -174+10log10(20e6)+7=-93.989700dBm; this is a theoretical configuration calculation, not measured/calibrated urban noise.

P is TTL-failed unique socket-accepted datagrams divided by mature accepted datagrams, with expiry in[t-1s,t). It includes late receives and does not mean PHY/permanent loss. L is mean timely mature RX-firstTX; no timely delivery is UNKNOWN. P09 gateway expected-sequence loss has a separate denominator/authority/version; commands reference actor-usable evidence and actual downlink receipts.

The numeric attempt/model/speed defaults above are source-resolved. Historical output lacks runtime getter values for them, actual TxVector, CCA/preamble/RTS/EDCA/aggregation attributes and socket buffers. Future instrumentation should export those settings without changing them. Old logs stay intact.

Rain variants need frequency, geometric path and physical rain rate. Existing weather.rain is a dimensionless authored ratio and cannot be silently used as mm/h. Shadow/fading are separate declared variants. The approved additions are preparation scope, not implemented/adopted RF results yet.

Evidence and exact primary-source URLs are in current_radio_checkpoint.json. Runtime path is retained there for server reproducibility.
