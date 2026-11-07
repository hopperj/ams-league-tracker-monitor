"""Deterministic synthetic game memory; never labelled as a real race."""
import math
from .schema import SharedMemory

NAMES = ["Alex Morgan", "Jamie Chen", "Sam Taylor", "Riley Santos", "Casey Brooks", "Jordan Lee"]


def snapshot(elapsed, driver_name="Alex Morgan", observer=0):
    s = SharedMemory()
    s.mVersion, s.mBuildVersionNumber = 14, 16000
    s.mGameState, s.mSessionState, s.mRaceState = 2, 5, 2
    s.mNumParticipants, s.mLapsInEvent = len(NAMES), 8
    s.mTrackLocation, s.mTrackVariation = b"Donington Park", b"Grand Prix"
    s.mTrackLength = 4020
    s.mAmbientTemperature, s.mTrackTemperature, s.mRainDensity = 19, 27, 0
    s.mSequenceNumber = int(elapsed * 60) * 2 + 2
    s.mFuelCapacity, s.mFuelLevel = 100, max(0.05, 0.72 - elapsed / 5000)
    s.mCarName, s.mCarClassName = b"Porsche 911 GT3 R", b"GT3 Gen2"
    names = list(NAMES)
    if driver_name not in names:
        names[observer % len(names)] = driver_name
    s.mViewedParticipantIndex = names.index(driver_name)
    for i, name in enumerate(names):
        lap_time = 90 + i * 0.8
        progress = max(0, elapsed - i * 1.3)
        lap, fraction = int(progress / lap_time), (progress % lap_time) / lap_time
        p = s.mParticipantInfo[i]
        p.mIsActive, p.mName, p.mRacePosition = True, name.encode(), i + 1
        p.mLapsCompleted, p.mCurrentLap = lap, lap + 1
        p.mCurrentSector = min(2, int(fraction * 3))
        p.mCurrentLapDistance = fraction * s.mTrackLength
        angle = fraction * math.tau
        p.mWorldPosition[:] = [math.cos(angle) * 500, 0, math.sin(angle) * 300]
        s.mCarNames[i].value, s.mCarClassNames[i].value = b"Porsche 911 GT3 R", b"GT3 Gen2"
        s.mSpeeds[i] = 32 + 25 * abs(math.sin(angle * 2 + i * 0.1))
        s.mLastLapTimes[i] = lap_time if lap else -1
        s.mFastestLapTimes[i] = lap_time if lap else -1
        s.mRaceStates[i], s.mPitModes[i] = 2, (2 if i == 3 and 290 < elapsed < 310 else 0)
        s.mCurrentSector1Times[i], s.mCurrentSector2Times[i], s.mCurrentSector3Times[i] = lap_time * .33, lap_time * .34, lap_time * .33
    own = s.mParticipantInfo[s.mViewedParticipantIndex]
    x = own.mCurrentLapDistance / s.mTrackLength * math.tau
    s.mSpeed = s.mSpeeds[s.mViewedParticipantIndex]
    s.mThrottle = max(0, min(1, math.sin(x * 3 + observer * .07) * .8 + .6))
    s.mBrake = max(0, math.cos(x * 3 + observer * .07) - .45)
    s.mSteering, s.mRpm, s.mGear = math.sin(x * 2) * .35, 5800 + s.mThrottle * 2400, 3 + int(s.mThrottle * 3)
    s.mCurrentTime, s.mLapInvalidated = elapsed % (90 + s.mViewedParticipantIndex * .8), False
    for i in range(4):
        s.mTyreTemp[i], s.mTyreWear[i], s.mAirPressure[i] = 82 + i, elapsed / 20000, 184.8
        s.mTyreCompound[i].value = b"Soft Slick"
    return bytes(s)
