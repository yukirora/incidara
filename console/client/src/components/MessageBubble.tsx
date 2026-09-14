import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

interface MessageBubbleProps {
  role: "user" | "assistant";
  text: string;
  kind?: "thinking";
  streaming?: boolean;
  timestamp?: string;
  userName?: string;
}

function formatTime(ts?: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
      d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

/** Render a diff code block with colored +/- lines */
function DiffBlock({ code }: { code: string }) {
  const lines = code.split("\n");
  return (
    <div className="diff-block rounded-md overflow-hidden border border-zinc-700 bg-zinc-900 my-2 text-xs font-mono">
      {lines.map((line, i) => {
        let bg = "";
        let textColor = "text-zinc-300";
        let prefix = " ";
        if (line.startsWith("+") && !line.startsWith("+++")) {
          bg = "bg-emerald-900/40";
          textColor = "text-emerald-300";
          prefix = "+";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          bg = "bg-red-900/40";
          textColor = "text-red-300";
          prefix = "-";
        } else if (line.startsWith("@@") || line.startsWith("+++") || line.startsWith("---")) {
          bg = "bg-blue-900/30";
          textColor = "text-blue-300";
        }
        return (
          <div key={i} className={`${bg} ${textColor} px-3 py-px leading-relaxed flex`}>
            <span className="select-none w-4 shrink-0 text-zinc-600 text-right mr-2">{prefix}</span>
            <span className="whitespace-pre-wrap break-all">{line}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Code block renderer that detects diff language */
function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const lang = className?.replace("language-", "") || "";
  const code = String(children).replace(/\n$/, "");

  if (lang === "diff") {
    return <DiffBlock code={code} />;
  }

  // Default code block
  return (
    <pre className="bg-zinc-900 text-zinc-200 rounded-md p-3 my-2 text-xs font-mono overflow-x-auto">
      <code>{code}</code>
    </pre>
  );
}

/** Markdown components override */
const markdownComponents: Components = {
  code({ className, children, ...props }) {
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      return <CodeBlock className={className} children={children} />;
    }
    // Inline code
    return (
      <code className="bg-zinc-100 text-zinc-700 px-1 py-0.5 rounded text-xs font-mono" {...props}>
        {children}
      </code>
    );
  },
  pre({ children }) {
    // Let the code component handle rendering; pre is just a wrapper
    return <>{children}</>;
  },
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline hover:text-blue-800">
        {children}
      </a>
    );
  },
  table({ children }) {
    return (
      <div className="overflow-x-auto my-2">
        <table className="min-w-full border-collapse border border-zinc-300 text-xs">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return <th className="border border-zinc-300 bg-zinc-100 px-2 py-1 text-left font-semibold">{children}</th>;
  },
  td({ children }) {
    return <td className="border border-zinc-300 px-2 py-1">{children}</td>;
  },
  blockquote({ children }) {
    return <blockquote className="border-l-3 border-zinc-300 pl-3 my-2 text-zinc-600 italic">{children}</blockquote>;
  },
};

export function MessageBubble({ role, text, kind, streaming, timestamp, userName }: MessageBubbleProps) {
  const isUser = role === "user";
  const isThinking = kind === "thinking";
  const time = formatTime(timestamp);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-2`}>
      <div className="max-w-[85%]">
        <div
          className={`
            rounded-xl px-3 py-2 text-sm leading-relaxed
            ${isUser
              ? "bg-zinc-800 text-white"
              : isThinking
                ? "bg-zinc-50 text-zinc-500 italic border border-zinc-200"
                : "bg-white text-zinc-800 border border-zinc-200 shadow-sm"
            }
          `}
        >
          {isUser || isThinking ? (
            <pre className="whitespace-pre-wrap font-sans break-words">
              {text}
              {streaming && (
                <span className="inline-block w-[0.5ch] bg-current animate-pulse ml-0.5">▊</span>
              )}
            </pre>
          ) : (
            <div className="markdown-body prose prose-sm prose-zinc max-w-none
              prose-headings:mt-3 prose-headings:mb-1 prose-headings:text-zinc-800
              prose-p:my-1 prose-p:leading-relaxed
              prose-li:my-0.5
              prose-code:before:content-none prose-code:after:content-none
              [&_.diff-block]:-mx-1">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {text}
              </ReactMarkdown>
              {streaming && (
                <span className="inline-block w-[0.5ch] bg-current animate-pulse ml-0.5">▊</span>
              )}
            </div>
          )}
        </div>
        <div className={`text-[10px] text-zinc-400 mt-0.5 ${isUser ? "text-right" : "text-left"} px-1`}>
          {userName && <span className="font-medium text-zinc-500">{userName}</span>}
          {userName && time && <span> · </span>}
          {time}
        </div>
      </div>
    </div>
  );
}
