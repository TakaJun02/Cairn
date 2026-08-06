import { ref, onMounted, onUnmounted } from 'vue';
import { calculateDistance } from './geoutils';

// シングルトンとして共有するリアクティブ値
const currentPos = ref(null);
// ダミー（isDebug=falseのためUIに出ないが、型合わせで返しておく）
const debugLat = ref(null);
const debugLng = ref(null);
const following = ref(false);

let watchId = null;
let restartTimerId = null;
let lastAcceptedPos = null;
let subscriberCount = 0;

const MIN_MOVE_METERS = 10; // GPSの微小な揺れを吸収するための閾値

const clearWatch = () => {
  if (watchId !== null) {
    navigator.geolocation.clearWatch(watchId);
    watchId = null;
  }
};

const clearRestartTimer = () => {
  if (restartTimerId) {
    clearTimeout(restartTimerId);
    restartTimerId = null;
  }
};

const stopAll = () => {
  clearWatch();
  clearRestartTimer();
  lastAcceptedPos = null;
};

const startWatch = () => {
  if (watchId !== null) return;
  if (!('geolocation' in navigator)) {
    console.warn('Geolocation API is not available in this environment.');
    return;
  }

  const watchOptions = {
    enableHighAccuracy: true,
    timeout: 30000,
    maximumAge: 15000,
  };

  watchId = navigator.geolocation.watchPosition(
    (pos) => {
      const nextPos = {
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
      };

      if (lastAcceptedPos) {
        const delta = calculateDistance(lastAcceptedPos, nextPos);
        if (delta < MIN_MOVE_METERS) {
          return;
        }
      }

      currentPos.value = nextPos;
      lastAcceptedPos = nextPos;
    },
    (err) => {
      console.error('Geolocation error:', err);

      if (err?.code === 3) {
        // TIMEOUT: 位置が取得できなかった場合はウォッチを再起動して再トライ
        scheduleRestart();
        return;
      }

      currentPos.value = null; // それ以外のエラー時はnullにする
      lastAcceptedPos = null;
    },
    watchOptions
  );
};

const scheduleRestart = () => {
  if (subscriberCount === 0) return;
  clearRestartTimer();
  restartTimerId = window.setTimeout(() => {
    restartTimerId = null;
    startWatch();
  }, 1000);
};

// 本番では isMock:false
const isMock = false;

// デバッグ用関数は no-op で返す（参照されても害がない）
const setDebugPos = () => {};
const toggleFollowing = () => {};

export function usePosition() {
  onMounted(() => {
    subscriberCount += 1;
    startWatch();
  });

  onUnmounted(() => {
    subscriberCount = Math.max(0, subscriberCount - 1);
    if (subscriberCount === 0) {
      stopAll();
    }
  });

  return {
    isMock,
    currentPos,
    debugLat,
    debugLng,
    following,
    setDebugPos,
    toggleFollowing,
  };
}
