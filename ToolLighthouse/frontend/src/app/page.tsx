import { CameraPanel } from "@/components/CameraPanel";
import { isClientBackendUrlEnabled } from "@/lib/server-config";

// Se evalúa en cada petición para que ALLOW_CLIENT_BACKEND_URL se lea en tiempo de ejecución.
export const dynamic = "force-dynamic";

export default function HomePage() {
  return (
    <main className="shell">
      <h1>ToolLighthouse</h1>
      <p className="lead">
        Apunta la cámara a una herramienta del laboratorio y el sistema dirá cuál es.
      </p>
      <CameraPanel allowBackendUrl={isClientBackendUrlEnabled()} />
    </main>
  );
}
