import type { Message } from "../types";
import ExecutionCard from "./ExecutionCard";

interface Props {
  message: Message;
}

export default function ChatMessage({ message }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`msg ${message.role}`}>
      <div className="msg-avatar">{isUser ? "J" : "B"}</div>
      <div className="msg-body">
        {message.execution ? (
          <>
            <span dangerouslySetInnerHTML={{ __html: message.text }} />
            <ExecutionCard card={message.execution} />
            {/* Post-execution summary for the demo assistant message */}
            {message.id === "m2" && (
              <span>
                Analysis complete. Found{" "}
                <strong>1,247 differentially expressed genes</strong> (padj &lt;
                0.05). Top upregulated gene: <code>SERPINA1</code> (log2FC =
                4.2).
                <br />
                <br />
                Results saved to <code>de_results.csv</code>. Click{" "}
                <strong>Artifacts</strong> in the top bar to preview the results
                table and MA plot.
              </span>
            )}
          </>
        ) : (
          <span dangerouslySetInnerHTML={{ __html: message.text }} />
        )}
      </div>
    </div>
  );
}
