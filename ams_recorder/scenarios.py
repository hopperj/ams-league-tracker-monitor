"""Deterministic, fictional full-SDK snapshots for the requested Spa test race."""
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import math

from .decoder import decode
from .schema import SharedMemory

NAMES = ('BootyWiz', 'EPaulD', 'Foote')
CAR, CLASS = 'Cadillac DPi-V.R', 'DPi'
TRACK_LENGTH = 7004.0
REGION_BYTES = 24576
# This fixed historical date is Atlantic Standard Time. Keeping the explicit
# offset also makes the fixture portable to Windows without a tzdata package.
START = datetime(2026, 11, 5, 21, 0, tzinfo=timezone(timedelta(hours=-4), 'AST'))
START_MS = round(START.timestamp() * 1000)


class SpaRace:
    laps = 12
    formation_seconds = 60
    tail_seconds = 30

    def __init__(self):
        # An illustrative speed profile, not surveyed Spa geometry or a
        # physical simulation. Integrate it so lap distance and speed agree.
        self.velocities = []
        corners = ((.015,.018,59),(.13,.03,30),(.32,.023,53),(.43,.026,48),
                   (.50,.03,43),(.61,.035,34),(.76,.025,25),(.94,.018,57))
        for i in range(1001):
            x=i/1000
            self.velocities.append(max(18,83-sum(a*math.exp(-((x-c)/w)**2) for c,w,a in corners)))
        self.times=[0.0]
        for a,b in zip(self.velocities,self.velocities[1:]):
            self.times.append(self.times[-1]+TRACK_LENGTH/1000/((a+b)/2))
        self.base_time=self.times[-1]
        self.pit_fraction=self.times[960]/self.base_time
        self.drivers=[]
        for index,(pace,pit_lap,pause) in enumerate(((123.5,6,22),(124.2,7,24),(125.8,5,400))):
            durations=[pace+math.sin(lap*1.71+index)*.65+(.6 if lap==1 else 0) for lap in range(1,17)]
            starts=[index*1.2]
            for lap,duration in enumerate(durations,1):
                starts.append(starts[-1]+duration+(pause if lap==pit_lap else 0))
            self.drivers.append({'pace':pace,'pit_lap':pit_lap,'pause':pause,'durations':durations,'starts':starts})
        winner=self.drivers[0]['starts'][self.laps]
        for driver in self.drivers:
            finish_laps=next(lap for lap in range(1,len(driver['starts'])) if driver['starts'][lap]>=winner)
            driver.update(finish_laps=finish_laps,finish_time=driver['starts'][finish_laps])
        self.end_seconds=math.ceil(max(d['finish_time'] for d in self.drivers))+self.tail_seconds

    def travel(self, fraction, duration):
        target=max(0,min(self.base_time-1e-9,fraction*self.base_time))
        index=min(999,max(0,bisect_right(self.times,target)-1))
        part=(target-self.times[index])/(self.times[index+1]-self.times[index])
        distance=(index+part)*TRACK_LENGTH/1000
        speed=(self.velocities[index]+part*(self.velocities[index+1]-self.velocities[index]))*self.base_time/duration
        return distance,speed

    def progress(self, elapsed, index):
        driver=self.drivers[index]
        if elapsed<0:
            return dict(completed=0,lap=0,distance=TRACK_LENGTH-1500+min(1498,(elapsed+60)*1498/60),speed=25,
                        state=1,lap_elapsed=0,invalid=False,pit=0,last=-1,best=-1,duration=driver['pace'],service=0)
        if elapsed>=driver['finish_time']:
            since=elapsed-driver['finish_time']
            return dict(completed=driver['finish_laps'],lap=driver['finish_laps']+1,distance=min(240,since*8),
                        speed=8 if since<30 else 0,state=3,lap_elapsed=since,invalid=False,pit=0,
                        last=driver['starts'][driver['finish_laps']]-driver['starts'][driver['finish_laps']-1],
                        best=min(driver['durations'][:driver['finish_laps']]),duration=driver['pace'],service=1)
        completed=max(0,bisect_right(driver['starts'],elapsed)-1)
        lap=completed+1;duration=driver['durations'][completed];within=max(0,elapsed-driver['starts'][completed])
        pit_start=duration*self.pit_fraction
        service=0.0;pit=0
        moving=within
        if lap==driver['pit_lap']:
            if pit_start<=within<pit_start+driver['pause']:
                moving=pit_start;service=(within-pit_start)/driver['pause'];pit=2
            elif within>=pit_start+driver['pause']:
                moving=within-driver['pause'];service=1;pit=3
            elif within>=pit_start-5:pit=1
        elif lap>driver['pit_lap']:
            service=1
            if lap==driver['pit_lap']+1 and within<4:pit=3
        distance,speed=self.travel(moving/duration,duration)
        if pit==2:speed=0
        last=driver['starts'][completed]-driver['starts'][completed-1] if completed else -1
        best=min((driver['durations'][j] for j in range(completed) if j+1!=driver['pit_lap']),default=-1)
        return dict(completed=completed,lap=lap,distance=distance,speed=speed,state=2,lap_elapsed=within,
                    invalid=index==1 and lap==3,pit=pit,last=last,best=best,duration=duration,service=service)

    def snapshot(self, elapsed, observer, sequence):
        s=SharedMemory();s.mVersion=14;s.mBuildVersionNumber=0
        s.mGameState=2;s.mSessionState=4 if elapsed<0 else 5
        s.mNumParticipants=3;s.mLapsInEvent=self.laps;s.mNumSectors=3
        s.mTrackLocation=b'Spa-Francorchamps';s.mTrackVariation=b'Grand Prix';s.mTrackLength=TRACK_LENGTH
        s.mTranslatedTrackLocation=s.mTrackLocation;s.mTranslatedTrackVariation=s.mTrackVariation
        s.mCarName=CAR.encode();s.mCarClassName=CLASS.encode();s.mSequenceNumber=sequence*2+2
        s.mViewedParticipantIndex=0;s.mSessionIsPrivate=True;s.mEnforcedPitStopLap=-1
        s.mSessionDuration=0;s.mSessionAdditionalLaps=0;s.mEventTimeRemaining=-1
        progress=[self.progress(elapsed,i) for i in range(3)]
        order=sorted(range(3),key=lambda i: (-progress[i]['completed']-progress[i]['distance']/TRACK_LENGTH,i))
        # Once each car finishes, the finishing order is stable.
        if all(p['state']==3 for p in progress):order=sorted(range(3),key=lambda i:(-self.drivers[i]['finish_laps'],self.drivers[i]['finish_time']))
        slots=[(observer+i)%3 for i in range(3)]
        for slot,index in enumerate(slots):
            p=progress[index];record=s.mParticipantInfo[slot]
            record.mIsActive=True;record.mName=NAMES[index].encode();record.mRacePosition=order.index(index)+1
            record.mLapsCompleted=p['completed'];record.mCurrentLap=p['lap'];record.mCurrentLapDistance=p['distance']
            record.mCurrentSector=min(2,int(p['distance']/TRACK_LENGTH*3))
            angle=p['distance']/TRACK_LENGTH*math.tau
            record.mWorldPosition[:]=[1100*math.sin(angle)+230*math.sin(3*angle),25+35*math.sin(angle-.6),900*math.cos(angle)]
            s.mCarNames[slot].value=CAR.encode();s.mCarClassNames[slot].value=CLASS.encode()
            s.mSpeeds[slot]=p['speed'];s.mOrientations[slot][:]=[0,.025*math.sin(angle),angle]
            s.mRaceStates[slot]=p['state'];s.mPitModes[slot]=p['pit'];s.mPitSchedules[slot]=1 if p['pit']==1 else 0
            s.mLastLapTimes[slot]=p['last'];s.mFastestLapTimes[slot]=p['best'];s.mLapsInvalidated[slot]=p['invalid']
            for sector,fraction in enumerate((.31,.43,.26),1):
                getattr(s,'mCurrentSector%dTimes'%sector)[slot]=p['duration']*fraction+(self.drivers[index]['pause'] if sector==3 and p['lap']==self.drivers[index]['pit_lap'] else 0)
                getattr(s,'mFastestSector%dTimes'%sector)[slot]=p['best']*fraction if p['best']>0 else -1
            s.mHighestFlagColours[slot]=1
        own=progress[observer];d=self.drivers[observer];x=own['distance']/TRACK_LENGTH
        next_p=self.progress(min(self.end_seconds,elapsed+.02),observer)
        acceleration=(next_p['speed']-own['speed'])/.02
        brake=min(1,max(0,-acceleration/11));throttle=0 if own['pit']==2 else max(0,min(1,1-brake*1.9+.06*math.sin(x*17+observer)))
        if elapsed>=d['finish_time']:throttle=.1 if own['speed'] else 0
        if own['pit']==2:brake=.15
        steer=.24*math.sin(x*18+observer*.035)+.09*math.sin(x*49)
        gear=min(6,max(1,int(own['speed']/14)+1)) if own['speed']>1 else 0
        rpm=1800 if gear==0 else min(7200,4300+2700*((own['speed']%14)/14))
        phase=x*math.tau;after_pit=own['service']
        consumed=(max(0,own['completed'])+x)*.028
        s.mRaceState=own['state'];s.mSpeed=s.mSpeeds[0];s.mGear=gear;s.mNumGears=6;s.mRpm=rpm;s.mMaxRPM=7200
        s.mThrottle=throttle;s.mBrake=brake;s.mClutch=1 if elapsed<0 else max(0,1-elapsed/.8)
        s.mSteering=steer;s.mUnfilteredThrottle=throttle;s.mUnfilteredBrake=brake;s.mUnfilteredSteering=steer;s.mUnfilteredClutch=s.mClutch
        s.mFuelCapacity=80;s.mFuelLevel=max(.02,min(.95,.62-consumed+after_pit*.25));s.mFuelPressureKPa=420
        s.mCurrentTime=own['lap_elapsed'];s.mLastLapTime=own['last'];s.mBestLapTime=own['best'];s.mLapInvalidated=own['invalid']
        s.mPersonalFastestLapTime=own['best'];s.mWorldFastestLapTime=min((p['best'] for p in progress if p['best']>0),default=-1)
        s.mHighestFlagColour=1;s.mPitMode=own['pit'];s.mPitSchedule=s.mPitSchedules[0];s.mCarFlags=3
        for sector,fraction in enumerate((.31,.43,.26),1):
            setattr(s,'mCurrentSector%dTime'%sector,own['duration']*fraction)
            for prefix in ('mFastestSector','mPersonalFastestSector','mWorldFastestSector'):
                setattr(s,prefix+str(sector)+'Time',own['best']*fraction if own['best']>0 else -1)
        s.mSplitTimeAhead=.4+observer*.8;s.mSplitTimeBehind=.6+(2-observer)*1.1
        s.mSplitTime=math.sin(x*4+observer)*.15;s.mOdometerKM=(own['completed']*TRACK_LENGTH+own['distance'])/1000
        s.mOilTempCelsius=100+7*throttle;s.mOilPressureKPa=350+150*throttle
        s.mWaterTempCelsius=88+4*throttle;s.mWaterPressureKPa=110
        s.mLastOpponentCollisionIndex=-1;s.mLastOpponentCollisionMagnitude=0
        s.mEngineSpeed=rpm*math.tau/60;s.mEngineTorque=620*throttle
        s.mOrientation[:]=s.mOrientations[0][:];s.mLocalVelocity[:]=[0,0,own['speed']]
        s.mWorldVelocity[:]=[own['speed']*math.cos(phase),0,-own['speed']*math.sin(phase)]
        s.mAngularVelocity[:]=[0,own['speed']/1100,0]
        s.mLocalAcceleration[:]=[steer*25,0,max(-15,min(8,acceleration))]
        s.mWorldAcceleration[:]=[s.mLocalAcceleration[0]*math.cos(phase),0,s.mLocalAcceleration[2]]
        s.mExtentsCentre[:]=[1,0.5,2.5]
        s.mAmbientTemperature=15+.4*math.sin(elapsed/600);s.mTrackTemperature=22+.8*math.sin(elapsed/500)
        s.mRainDensity=0;s.mWindSpeed=2+.5*math.sin(elapsed/30);s.mWindDirectionX=.6;s.mWindDirectionY=.8;s.mCloudBrightness=.75
        s.mBrakeBias=.56+observer*.005;s.mAntiLockSetting=0;s.mTractionControlSetting=3+observer
        collision=observer==2 and own['lap']==d['pit_lap'] and own['distance']>TRACK_LENGTH*.92
        damaged=observer==2 and own['lap']>=d['pit_lap']
        s.mAeroDamage=(.65*(1-after_pit)+.05*after_pit) if damaged else .005*observer
        s.mEngineDamage=.025 if damaged else 0
        if collision and own['pit']==1:
            s.mCrashState=1;s.mLastOpponentCollisionIndex=1;s.mLastOpponentCollisionMagnitude=3.5
            s.mHighestFlagColour=5;s.mHighestFlagColours[0]=5
        for wheel in range(4):
            temp=78+10*throttle+6*abs(steer)+wheel*.6-(12 if own['pit']==2 else 0)
            wear=max(0,(own['completed']+x-(d['pit_lap'] if after_pit else 0))*.007)
            s.mTyreFlags[wheel]=1;s.mTerrain[wheel]=0;s.mTyreY[wheel]=-.3;s.mTyreRPS[wheel]=own['speed']/(math.tau*.33)
            s.mTyreSlipSpeed[wheel]=brake*.12+throttle*.025;s.mTyreTemp[wheel]=temp;s.mTyreGrip[wheel]=max(.8,1-wear)
            s.mTyreLateralStiffness[wheel]=1;s.mTyreWear[wheel]=wear
            s.mBrakeDamage[wheel]=.002*own['completed'];s.mSuspensionDamage[wheel]=.15*(1-after_pit) if damaged and wheel==0 else 0
            s.mBrakeTempCelsius[wheel]=300+500*brake+wheel*9
            s.mTyreTreadTemp[wheel]=temp+273.15;s.mTyreLayerTemp[wheel]=temp+270.15;s.mTyreCarcassTemp[wheel]=temp+266.15
            s.mTyreRimTemp[wheel]=temp+235.15;s.mTyreInternalAirTemp[wheel]=temp+250.15
            s.mWheelLocalPositionY[wheel]=-.3;s.mSuspensionTravel[wheel]=.035+.012*math.sin(x*60+wheel)
            s.mSuspensionVelocity[wheel]=.02*math.cos(x*60+wheel);s.mAirPressure[wheel]=174+(temp-65)*.55
            s.mRideHeight[wheel]=.048+.009*math.sin(x*34+wheel);s.mTyreTempLeft[wheel]=temp+3;s.mTyreTempCenter[wheel]=temp;s.mTyreTempRight[wheel]=temp-2
            s.mTyreCompound[wheel].value=b'Soft Slick'
        return bytes(s).ljust(REGION_BYTES,b'\0')

    def frame(self, sequence, observer, hz=60):
        elapsed=sequence/hz-self.formation_seconds
        frame=decode(self.snapshot(elapsed,observer,sequence),NAMES[observer])
        frame.update(sequence=sequence,captured_at_ms=START_MS+round(elapsed*1000),clock_uncertainty_ms=2+observer,
                     source_kind='shared_memory',consistent=True,synthetic=True,replay=False)
        return frame

    def sample_count(self,hz=60):
        return int((self.formation_seconds+self.end_seconds)*hz)+1
