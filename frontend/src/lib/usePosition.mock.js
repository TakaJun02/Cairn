// frontend/src/lib/usePosition.mock.js
import { ref, onMounted, onUnmounted } from 'vue';

/**
 * デバッグ用現在地フック（動的経路設定対応版）
 * - バックエンドから取得した経路をシミュレーション経路として設定可能にする
 * - setMockTrack() を介して外部から経路を注入する
 */

// デフォルトまたは外部から設定された経路
let MOCK_TRACK = [
  [140.073769, 39.393477],
  [140.07381, 39.39352],
  [140.07385, 39.39356],
];

const currentPos = ref(null);
const debugLat = ref(MOCK_TRACK[0][1]);
const debugLng = ref(MOCK_TRACK[0][0]);

// --- ジャーニー・シミュレーション制御 ---
const isJourneyInProgress = ref(false);
let journeyTimerId = null;
let trackIndex = 0;

const stepJourney = () => {
  if (trackIndex >= MOCK_TRACK.length) {
    stopJourney();
    return;
  }

  const [lng, lat] = MOCK_TRACK[trackIndex];
  debugLat.value = lat;
  debugLng.value = lng;
  currentPos.value = { lat, lng };
  
  trackIndex++;
};

const startJourney = () => {
  if (isJourneyInProgress.value) return;
  isJourneyInProgress.value = true;
  stepJourney(); 
  journeyTimerId = setInterval(stepJourney, 300);
};

const stopJourney = () => {
  if (!isJourneyInProgress.value) return;
  isJourneyInProgress.value = false;
  if (journeyTimerId) {
    clearInterval(journeyTimerId);
    journeyTimerId = null;
  }
};

const resetJourney = () => {
  stopJourney();
  trackIndex = 0;
  if (MOCK_TRACK && MOCK_TRACK.length > 0) {
    const [lng, lat] = MOCK_TRACK[0];
    debugLat.value = lat;
    debugLng.value = lng;
    currentPos.value = { lat, lng };
  }
};

// ★ 新しい関数：外部から経路を設定する
const setMockTrack = (newTrack) => {
  console.log('[usePosition.mock] Received new track with', newTrack?.length, 'points');
  if (!newTrack || newTrack.length === 0) {
    return;
  }
  // polylineは[lng, lat]形式なので、そのまま利用
  MOCK_TRACK = newTrack;
  resetJourney();
};


// --- 互換機能 ---
const following = isJourneyInProgress; 
const toggleFollowing = () => {
  if (isJourneyInProgress.value) {
    stopJourney();
  } else {
    startJourney();
  }
};

const setDebugPos = (lat, lng) => {
  const la = Number(lat);
  const ln = Number(lng);
  if (!Number.isFinite(la) || !Number.isFinite(ln)) return;
  if (isJourneyInProgress.value) {
    stopJourney();
  }
  debugLat.value = la;
  debugLng.value = ln;
  currentPos.value = { lat: la, lng: ln };
};


let subscriberCount = 0;

export function usePosition() {
  onMounted(() => {
    subscriberCount++;
    if (!currentPos.value) {
      resetJourney();
    }
  });

  onUnmounted(() => {
    subscriberCount = Math.max(0, subscriberCount - 1);
    if (subscriberCount === 0) {
      stopJourney();
    }
  });

  return {
    isMock: true,
    currentPos,
    
    // デバッグUI用
    debugLat,
    debugLng,
    
    // ジャーニー制御
    isJourneyInProgress,
    startJourney,
    stopJourney,
    resetJourney,
    setMockTrack, // ★ UIから使えるように公開

    // 互換用
    following,
    toggleFollowing,
    setDebugPos,
  };
}
