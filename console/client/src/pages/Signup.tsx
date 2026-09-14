import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

const GATEWAY_URL = "http://192.0.2.10:8200";

export function Signup() {
  const navigate = useNavigate();

  useEffect(() => {
    // Gateway handles both login and signup — redirect there
    const callbackUrl = `${window.location.origin}/api/auth/gateway/callback`;
    window.location.href = `${GATEWAY_URL}/login?next=${encodeURIComponent(callbackUrl)}`;
  }, [navigate]);

  return (
    <div className="flex items-center justify-center min-h-screen bg-zinc-50">
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-sm border border-zinc-200/80 p-8 text-center">
        <div className="text-3xl mb-2">⚡</div>
        <h1 className="text-xl font-semibold text-zinc-900">Incidara</h1>
        <p className="text-sm text-zinc-500 mt-1">Redirecting to LTP login…</p>
      </div>
    </div>
  );
}
