/** "multimetro_digital" -> "multimetro digital". Para mostrar nombres de clase al usuario. */
export function formatClass(name: string): string {
  return name.replace(/_/g, " ");
}
