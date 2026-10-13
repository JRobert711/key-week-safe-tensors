"use client";

interface Props {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function BackendUrlField({ value, onChange, disabled }: Props) {
  return (
    <label className="field">
      <span>URL del backend</span>
      <input
        type="url"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        placeholder="http://127.0.0.1:8000"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
      <small className="hint">
        Pega solo el host (se añade /detect) o la URL completa del endpoint.
      </small>
    </label>
  );
}
