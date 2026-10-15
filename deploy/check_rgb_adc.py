from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataclasses import replace
from statistics import median
import json,time
from JETSON.Sensor import DEFAULT_CONFIG,DeviceService
cfg=DEFAULT_CONFIG
names=('dht11','light','ultrasonic','smoke','oled','servo','stepper','buzzer','lock','fan','body_temperature')
cfg=replace(cfg,**{n:replace(getattr(cfg,n),enabled=False) for n in names})
report=[]
with DeviceService(config=cfg) as device:
    for color in ('off','red','green','blue','white','off'):
        print('RGB_COLOR='+color,flush=True)
        device.control('rgb',color=color)
        time.sleep(.3)
        samples=[]
        errors=[]
        started=time.monotonic()
        while time.monotonic()-started<5:
            try:
                samples.append(device.manager.adc_monitor.read_channel(3))
            except Exception as exc:
                errors.append(str(exc))
            time.sleep(.02)
        volts=[x['volts'] for x in samples]
        result={'color':color,'channel':3,'count':len(samples),'errors':errors,
                'minimum_volts':min(volts) if volts else None,
                'median_volts':median(volts) if volts else None,
                'maximum_volts':max(volts) if volts else None,
                'diagnostic':device.check('rgb'),'samples':samples}
        report.append(result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('samples','diagnostic')},ensure_ascii=False),flush=True)
Path('/home/dianxin/usb-hub-test/rgb-a3-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
