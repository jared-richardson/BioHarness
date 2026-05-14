import { Send, Loader2 } from "lucide-react";
import { useState, useRef, useCallback, useEffect } from "react";
import { useApp } from "../context";

const HINTS = [
  { label: "Pathway enrichment", text: "Run pathway enrichment on the DE results" },
  { label: "Volcano plot", text: "Generate a volcano plot" },
  { label: "Export report", text: "Export results as a Quarto report" },
  { label: "Explain top genes", text: "Explain the top 10 DE genes" },
];

const autoResize = (el: HTMLTextAreaElement) => {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
};

export default function ChatInput() {
  const [value, setValue] = useState("");
  const textRef = useRef<HTMLTextAreaElement>(null);
  const { handleSend, sending, pendingPrompt, setPendingPrompt } = useApp();

  useEffect(() => {
    if (pendingPrompt) {
      setValue(pendingPrompt);
      setPendingPrompt(null);
      if (textRef.current) {
        textRef.current.focus();
        setTimeout(() => autoResize(textRef.current!), 0);
      }
    }
  }, [pendingPrompt, setPendingPrompt]);

  const onSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || sending) return;
    handleSend(trimmed);
    setValue("");
    if (textRef.current) {
      textRef.current.style.height = "auto";
    }
  }, [value, sending, handleSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const fillInput = (text: string) => {
    setValue(text);
    if (textRef.current) {
      textRef.current.focus();
      setTimeout(() => autoResize(textRef.current!), 0);
    }
  };

  return (
    <div className="chat-input-area">
      <div className="chat-input-row">
        <textarea
          ref={textRef}
          className="chat-input"
          placeholder="Ask Bio-Harness to analyze, execute, or explain..."
          rows={1}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            autoResize(e.target);
          }}
          onKeyDown={handleKeyDown}
          disabled={sending}
        />
        <button className="send-btn" onClick={onSend} disabled={sending}>
          {sending ? <Loader2 size={18} className="spin" /> : <Send size={18} />}
        </button>
      </div>
      <div className="input-hints">
        {HINTS.map((h) => (
          <span
            key={h.label}
            className="input-hint"
            onClick={() => fillInput(h.text)}
          >
            {h.label}
          </span>
        ))}
      </div>
    </div>
  );
}
