# H3.0 curved-finger scalar resultant test plan

## Measurement meaning

The primary value is a scalar model fitted directly from the 24 RAW channels to the external force-gauge reading. It is not a magnitude computed from firmware Fx/Fy/Fz. The firmware components remain diagnostic fields because Fx/Fy do not have traceable three-axis calibration.

The old NO8 coefficients in v5.0 are an initial-check model only. Any changed silicone, magnet, sensor relative position, or curved shape requires new H3 data and a new final model.

## Non-negotiable acceptance requirements

- Front, middle and rear contact positions must use one shared calibration model. Position-specific coefficients are not accepted as a way to hide inconsistency.
- After 30 N for 500 s and rapid unloading, the absolute resultant peak during the first 5 s must be no more than 1.5 N.
- During the final 10 s of the 120 s unloaded recovery, **every recorded sample** must satisfy `abs(resultant) <= 0.01 N` without a second tare.
- Electromagnetic interference must not create a steady shift above 0.01 N, a transient deviation above 1.5 N, a paired-frame gap above 1.0 s, a magnetic-coherence warning, or a low-confidence warning.
- These are hard PASS/FAIL gates. Software filtering must not be used to conceal a failing raw signal.

The current provisional NO8 model has a low-signal dead zone and is therefore **not acceptance-eligible** for the 0.01 N or EMI claims. It may collect development data, but the monitor deliberately forces a failed quality gate until a dead-zone-free H3 model is installed with status `FINAL_H3_VALIDATED_NO_DEADBAND_0P01N`.

## Front/middle/rear accuracy and hysteresis

1. Install the finger at one fixed nominal position.
2. Keep it unloaded and perform exactly one **空载 RAW 置零**.
3. Start `前/中/后同模型精度/迟滞 0→30→0 N`; the UI locks tare until the test ends.
4. Follow 0, 1, 2, ..., 30, 29, ..., 1, 0 N at the **front**, then repeat at the **middle**, then repeat at the **rear**. The full run has 183 points and never switches models.
5. At every point, wait until the force-gauge reading is stable, enter its actual scalar reading, and click **载荷稳定，采样 3 秒**.
6. Do not tare, reseat, or change the nominal contact location mid-round.
7. Export CSV. The continuous file identifies each 3 s stable window; `_stable3s_summary.csv` stores measured reference resultant, software resultant median, error, confidence, firmware raw Fz median, and sample count.

Compare equal `position_point_index` rows across front/middle/rear to quantify position consistency. Repeat the whole round after re-installation to quantify fixture-angle repeatability. Do not combine re-installations into one tare session.

## Long-hold recovery (independent test)

1. Finish/stop any accuracy test.
2. Unload the finger, let RAW stabilize, and perform a new **空载 RAW 置零**.
3. Apply 30 N and start `长按恢复 30 N×500 s→0 N×120 s`.
4. Hold 30 N for 500 s. The monitor records continuously.
5. When prompted, unload rapidly to 0 N and immediately click **已快速卸载到 0 N**.
6. Keep unloaded for 120 s. Do not tare after unloading.
7. Export the continuous CSV. The acceptance summary automatically reports the first-5-second absolute peak, final-10-second median and maximum absolute residual, maximum sample gap, quality faults and final PASS/FAIL.

Passing requires first-5-second peak `<= 1.5 N` and final-10-second maximum absolute residual `<= 0.01 N`. A new tare after unloading is prohibited.

## Electromagnetic-immunity test (independent runs)

1. Unload and establish a fresh RAW tare.
2. Select `电磁抗扰 空载基线30 s→干扰60 s`, select the front/middle/rear position, and start.
3. Keep all mechanical conditions unchanged for the 30 s no-interference baseline.
4. When prompted, write the exact interference source in **备注**, enable it, and click `备注已填，开启干扰`.
5. Maintain the interference for 60 s without touching the finger, cable or fixture.
6. Repeat as separate runs for at least: CH342F USB traffic/cable repositioning, nearby switching supply, motor/relay switching, maximum-power phone/Wi-Fi radio transmission, and the intended product enclosure/cable routing. Formal ESD/EFT/radiated-RF tests require suitable laboratory equipment.
7. A run passes only if steady shift is `<= 0.01 N`, transient deviation is `<= 1.5 N`, maximum paired-frame gap is `<= 1.0 s`, and no magnetic-coherence or low-confidence quality fault occurs.

“No EMI effect” may be claimed only after every defined physical scenario passes on representative hardware. A host-side simulation cannot establish electromagnetic immunity.

## Remaining H3 accuracy limits

Do not reuse NO8 offline MAE as an H3 acceptance limit. The recovery and EMI limits above are already fixed requirements. Define the remaining loaded-range accuracy, hysteresis and front/middle/rear consistency limits after collecting representative H3 fingers, installations and full loading/unloading paths.
