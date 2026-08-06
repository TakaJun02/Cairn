<template>
  <div ref="mapContainer" class="map-container"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';

// Leafletのデフォルトアイコン問題を修正
import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png';
import iconUrl from 'leaflet/dist/images/marker-icon.png';
import shadowUrl from 'leaflet/dist/images/marker-shadow.png';

L.Icon.Default.mergeOptions({
  iconRetinaUrl,
  iconUrl,
  shadowUrl,
});

const TILE_URL_TEMPLATE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';

const esriWorldStreet = L.tileLayer(
  TILE_URL_TEMPLATE,
  {
    attribution:
      "Tiles &copy; Esri — Source: Esri, HERE, Garmin, FAO, NOAA, USGS, EPA, NPS",
    maxZoom: 19
  }
);

const props = defineProps({
  plan: {
    type: Object,
    required: true,
  },
  // NavViewから現在地情報を受け取るためのprop
  currentPos: {
    type: Object,
    default: null,
  }
});

const emit = defineEmits(['user-pan']);

const mapContainer = ref(null);
const map = ref(null);
const userLocationMarker = ref(null); // ★ ref() でラップ
let routeLayer = null;
let poiMarkers = [];
let isProgrammaticMove = false;
let programmaticResetTimer = null;
let containerResizeObserver = null;

const scheduleProgrammaticReset = (delay = 800) => {
  if (programmaticResetTimer) {
    clearTimeout(programmaticResetTimer);
    programmaticResetTimer = null;
  }
  programmaticResetTimer = window.setTimeout(() => {
    isProgrammaticMove = false;
    programmaticResetTimer = null;
  }, delay);
};

const markProgrammaticMove = (delay = 800) => {
  isProgrammaticMove = true;
  scheduleProgrammaticReset(delay);
};

const flyToSpot = (lat, lon, zoom = undefined, flyOptions = {}) => {
  if (!map.value) return;
  let targetZoom;
  if (typeof zoom === 'number') {
    targetZoom = zoom;
  } else if (zoom === null) {
    targetZoom = map.value.getZoom();
  } else {
    targetZoom = 16;
  }
  markProgrammaticMove(Math.max(800, (flyOptions.duration ?? 1) * 1200));
  map.value.flyTo([lat, lon], targetZoom, {
    animate: true,
    duration: 1,
    ...flyOptions,
  });
};

// ===========================================
// ★★★ ここからが修正箇所です ★★★
// ===========================================

// NavViewから渡された座標でマーカーを更新する関数
const updateCurrentPosition = (lat, lng) => {
  if (!map.value) return;
  const latlng = L.latLng(lat, lng);

  if (userLocationMarker.value) {
    // 既存マーカーの位置を更新
    userLocationMarker.value.setLatLng(latlng);
  } else {
    // マーカーがまだなければ作成
    userLocationMarker.value = L.marker(latlng, {
      icon: L.divIcon({
        className: 'current-position-marker',
        html: '<div class="pulse"></div>',
        iconSize: [20, 20],
      }),
    }).addTo(map.value);
  }
};

// 親コンポーネントから呼び出せるように関数を公開
defineExpose({ 
  flyToSpot,
  updateCurrentPosition // この関数を公開
});

// ===========================================
// ★★★ 修正箇所はここまで ★★★
// ===========================================

const drawRoute = () => {
  if (routeLayer) {
    map.value.removeLayer(routeLayer);
  }
  if (props.plan && props.plan.route) {
    // frontend_design_system.md §8.3.1-4 / §12.1: 地図は明るい図版なので、
    // 経路線は「濃くする」で可読性を上げる(白い縁取り=casing は不採用。
    // §12.1 に理由あり: Leaflet は 1 本の path に 2 つの stroke を持てず、
    // 同じ GeoJSON を上へもう 1 層描くことになりレイヤのライフサイクルが
    // 増えるため)。色は design-system.css のトークンに揃える
    // (#2f4fd8 = §8.3.1 表の「深い藍」。#12756a = --color-signal-deep と
    // 同値。Leaflet の SVG 属性へ渡す値なので、ここでは色トークンを
    // 直接 hex で複製する)。
    const styleFunction = (feature) => {
      const mode = feature?.properties?.mode;
      if (mode === 'car') return { color: '#2f4fd8', weight: 5, opacity: 0.95 };
      if (mode === 'foot') return { color: '#12756a', weight: 4, opacity: 0.95, dashArray: '5, 10' };
      return { color: '#2f4fd8', weight: 5, opacity: 0.95 };
    };
    routeLayer = L.geoJSON(props.plan.route, { style: styleFunction }).addTo(map.value);
    markProgrammaticMove();
    map.value.fitBounds(routeLayer.getBounds());
  }
};

// frontend_design_system.md §8.3.1-5 / §12.1: POI マーカーのアイコンを
// Leaflet 既定の無番号ピンから、番号入りの divIcon へ差し替える。番号・色は
// NavView.vue の旅程ストリップ(`sortedWaypoints` のバッジ)と一致させ、
// 地図とストリップを目で結ぶ。along_pois(近くのおすすめ)はストリップ側にも
// 番号が無いので、ここでも無番号の小さな点にする。
const POI_ICON_SIZE = 26;
const poiIconCache = new Map();

function numberedPoiIcon(number) {
  const cached = poiIconCache.get(number);
  if (cached) return cached;
  const icon = L.divIcon({
    className: 'poi-marker poi-marker--numbered',
    html: `<div class="poi-marker-badge">${number}</div>`,
    iconSize: [POI_ICON_SIZE, POI_ICON_SIZE],
    iconAnchor: [POI_ICON_SIZE / 2, POI_ICON_SIZE / 2],
  });
  poiIconCache.set(number, icon);
  return icon;
}

let plainPoiIcon = null;
function nearbyPoiIcon() {
  if (!plainPoiIcon) {
    plainPoiIcon = L.divIcon({
      className: 'poi-marker poi-marker--plain',
      html: '<div class="poi-marker-dot"></div>',
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
  }
  return plainPoiIcon;
}

const drawPois = () => {
  poiMarkers.forEach(marker => map.value.removeLayer(marker));
  poiMarkers = [];
  if (!props.plan) return;

  const addPoiMarker = (poi, icon) => {
    if (!poi || typeof poi.lat !== 'number' || typeof poi.lon !== 'number') return;
    const marker = L.marker([poi.lat, poi.lon], {
      interactive: false,
      icon,
    }).addTo(map.value);
    poiMarkers.push(marker);
  };

  // 番号は配列の並び順で 1 から振る。NavView.vue の `sortedWaypoints` は
  // `nearest_idx` で安定ソートするが、その値は現状どの経路でも設定されない
  // ため、実質この waypoints_info の並び順と常に一致する(同じ配列を
  // 参照しているため、番号の対応が崩れない)。
  if (props.plan.waypoints_info) {
    props.plan.waypoints_info.forEach((poi, index) => {
      addPoiMarker(poi, numberedPoiIcon(index + 1));
    });
  }
  if (props.plan.along_pois) {
    props.plan.along_pois.forEach((poi) => addPoiMarker(poi, nearbyPoiIcon()));
  }
};

const setupMap = () => {
  if (mapContainer.value && !map.value) {
    map.value = L.map(mapContainer.value, { zoomControl: false });
    markProgrammaticMove();
    map.value.setView([39.145, 140.102], 10);
    esriWorldStreet.addTo(map.value);
    L.control.scale({ imperial: false, metric: true }).addTo(map.value);
    if (map.value.attributionControl) {
      map.value.attributionControl.setPrefix('');
    }

    map.value.on('movestart', () => {
      if (!isProgrammaticMove) {
        emit('user-pan');
      }
    });

    map.value.on('moveend', () => {
      if (isProgrammaticMove) {
        esriWorldStreet.setUrl(TILE_URL_TEMPLATE);
      }
      scheduleProgrammaticReset(200);
    });

    drawRoute();
    drawPois();

    // ガイダンスマップの全画面トグル(NavWindow.vue)はコンテナのサイズを
    // CSS トランジション(width/height, 0.38s)で変化させるだけで、window の
    // resize イベントは発生しない。Leaflet はマウント時のコンテナサイズしか
    // 見ていないため、このままだと切替後にグレーの未描画領域が残る
    // (実機確認 2026-08-06)。ResizeObserver でコンテナ自体の実サイズ変化を
    // 監視し、変化のたびに invalidateSize() を呼んで追従させる。トランジション
    // 中は複数回発火するが、最終的に遷移後のサイズでも呼ばれるため通常の
    // ウィンドウリサイズと同様に正しいサイズへ収束する。
    if (typeof ResizeObserver !== 'undefined') {
      containerResizeObserver = new ResizeObserver(() => {
        map.value?.invalidateSize();
      });
      containerResizeObserver.observe(mapContainer.value);
    }

    // ★★★ デバッグ中はNavViewから位置情報を受け取るため、ここでの位置情報追跡は不要
    // startTracking();
  }
};

/*
// ★ このコンポーネント自身での位置情報追跡は不要になるためコメントアウト
const startTracking = () => {
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        updateCurrentPosition(latitude, longitude); // 修正後の関数を呼ぶ
      },
      (error) => { console.error('Geolocation error:', error); },
      { enableHighAccuracy: true }
    );
  } else {
    console.error('Geolocation is not supported by this browser.');
  }
};
*/

onMounted(() => {
  setupMap();
});

onBeforeUnmount(() => {
  if (containerResizeObserver) {
    containerResizeObserver.disconnect();
    containerResizeObserver = null;
  }
  if (map.value) {
    map.value.remove();
    map.value = null;
  }
  if (programmaticResetTimer) {
    clearTimeout(programmaticResetTimer);
    programmaticResetTimer = null;
  }
});

watch(() => props.plan, () => {
  if (map.value) {
    drawRoute();
    drawPois();
  }
}, { deep: true });
</script>

<style>
/* scopedを外してグローバルに適用 */
.map-container {
  width: 100%;
  height: 100%;
}

.leaflet-control-attribution {
  font-size: 0.7rem;
  padding: 3px 8px;
  background: rgba(15, 23, 42, 0.65);
  color: #e2e8f0;
  border-radius: 999px;
  box-shadow: 0 8px 14px rgba(15, 23, 42, 0.25);
  line-height: 1.2;
}

.leaflet-control-attribution a {
  color: rgba(148, 197, 255, 0.85);
}

/* POI マーカー(frontend_design_system.md §8.3.1-5 / §12.1)。
   番号入りバッジは旅程ストリップの `.stop-chip__num`(NavView.vue)と
   同じ色・同じ数字にして、地図とストリップを目で結ぶ。--color-signal-deep
   は明るい地図面の上で使う碧(§3.2)。 */
.poi-marker-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 9999px;
  background: var(--color-signal-deep, #12756a);
  color: var(--color-paper, #eef2f4);
  border: 2px solid var(--color-paper, #eef2f4);
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.35);
  font-family: var(--font-display, "Space Grotesk", sans-serif);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}
.poi-marker-dot {
  width: 10px;
  height: 10px;
  margin: 2px;
  border-radius: 9999px;
  background: var(--color-signal-deep, #12756a);
  border: 2px solid var(--color-paper, #eef2f4);
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.3);
}

/* 現在地マーカーのスタイル */
.current-position-marker .pulse {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #007bff;
  border: 2px solid #fff;
  box-shadow: 0 0 0 rgba(0, 123, 255, 0.4);
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(0, 123, 255, 0.7);
  }
  70% {
    transform: scale(1);
    box-shadow: 0 0 0 10px rgba(0, 123, 255, 0);
  }
  100% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(0, 123, 255, 0);
  }
}
</style>
