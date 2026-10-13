/** Carries delivered chat replies from Electron Main into the renderer's pet activity queue.
 * Realtime playback and Task lifecycles have their own contracts and do not emit pet notices.
 */
export interface PetActivity {
  id: string;
  kind: "chat_reply";
  message: string;
  createdAt: string;
}
