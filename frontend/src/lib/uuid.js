/**
 * localStorageからデバイスUUIDを取得します。
 * UUIDが存在しない場合は新しく生成して保存します。
 * @returns {string} デバイスUUID
 */
export function getDeviceUUID() {
  const UUID_KEY = 'deviceUUID';
  let deviceUUID = localStorage.getItem(UUID_KEY);

  if (!deviceUUID) {
    // localStorageにUUIDがなければ、新しく生成する
    deviceUUID = crypto.randomUUID();
    // 生成したUUIDをlocalStorageに保存して永続化する
    localStorage.setItem(UUID_KEY, deviceUUID);
  }

  return deviceUUID;
}
