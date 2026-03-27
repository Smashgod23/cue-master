import { useState, useRef, useCallback } from "react";
import { useNavigate, Link } from "react-router-dom";

const ACCEPTED_TYPES = {
  "application/pdf": ".pdf",
  "text/plain": ".txt",
  "image/png": ".png",
  "image/jpeg": ".jpg",
};

const ACCEPTED_EXTENSIONS = Object.values(ACCEPTED_TYPES).join(", ");

export default function Upload() {
  const navigate = useNavigate();
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [fileName, setFileName] = useState(null);

  const handleFile = useCallback(async (file) => {
    if (!file) return;

    // Validate file type
    const ext = file.name.split(".").pop().toLowerCase();
    const validExts = ["pdf", "txt", "png", "jpg", "jpeg"];
    if (!validExts.includes(ext)) {
      setError(`Unsupported file type (.${ext}). Please use ${ACCEPTED_EXTENSIONS}.`);
      return;
    }

    setError(null);
    setFileName(file.name);
    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch("/api/upload", { method: "POST", body: formData });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed (${res.status})`);
      }

      // Store parsed script in sessionStorage so Setup page can reference it
      const parsed = await res.json();
      sessionStorage.setItem("parsedScript", JSON.stringify(parsed));
      navigate("/setup");
    } catch (err) {
      setError(err.message);
      setUploading(false);
    }
  }, [navigate]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer?.files?.[0];
    handleFile(file);
  }, [handleFile]);

  const onDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      {/* Header */}
      <header className="px-6 py-4 border-b border-parchment-deep bg-parchment-warm/60">
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
              <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
              </svg>
            </div>
            <div>
              <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
              <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">Upload Your Script</span>
            </div>
          </Link>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <div className="max-w-xl w-full animate-fade-in-up">
          <h2 className="font-serif text-2xl sm:text-3xl font-bold text-ink text-center">
            Bring your script to the stage
          </h2>
          <p className="font-body text-sm text-ink-soft text-center mt-3 max-w-md mx-auto">
            Drop a PDF, text file, or photo of your script pages below.
            We'll parse the dialogue and stage directions automatically.
          </p>

          {/* Drop zone */}
          <div
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onClick={() => !uploading && inputRef.current?.click()}
            className={`mt-8 border-2 border-dashed rounded-2xl p-12 text-center transition-all duration-200 cursor-pointer
              ${dragging
                ? "border-gold bg-gold/5 scale-[1.01]"
                : "border-parchment-deep hover:border-warmgray-light bg-parchment-warm/30"
              }
              ${uploading ? "pointer-events-none opacity-70" : ""}
            `}
          >
            {uploading ? (
              <div className="flex flex-col items-center gap-4">
                {/* Spinning indicator */}
                <div className="w-12 h-12 rounded-full border-3 border-parchment-deep border-t-crimson animate-spin" />
                <p className="font-serif text-lg text-ink italic">
                  The Dramaturg is reading your script...
                </p>
                <p className="font-sans text-xs text-warmgray">{fileName}</p>
              </div>
            ) : (
              <>
                <div className="w-14 h-14 mx-auto rounded-xl bg-parchment-deep/40 flex items-center justify-center mb-4">
                  <svg className="w-7 h-7 text-warmgray" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="17 8 12 3 7 8" />
                    <line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                </div>
                <p className="font-serif text-lg text-ink">
                  Drop your script here
                </p>
                <p className="font-sans text-xs text-warmgray mt-2">
                  or click to browse — accepts {ACCEPTED_EXTENSIONS}
                </p>
              </>
            )}
          </div>

          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.txt,.png,.jpg,.jpeg"
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />

          {/* Error */}
          {error && (
            <div className="mt-4 p-4 rounded-xl bg-crimson/5 border border-crimson/20 text-center animate-fade-in-up">
              <p className="font-body text-sm text-crimson">{error}</p>
            </div>
          )}

          {/* Back link */}
          <div className="mt-8 text-center">
            <Link
              to="/"
              className="font-sans text-xs text-warmgray hover:text-ink-soft transition-colors no-underline"
            >
              &larr; Back to home
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
