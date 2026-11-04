/** True on a touch device (a coarse primary pointer): it has no c/i/p/u or n keys, so the copy
    that names them stays for a keyboard. False where matchMedia does not exist (a test, SSR). */
export function coarsePointer(): boolean {
  return typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches;
}
