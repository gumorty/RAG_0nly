import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "RAG 知识库管理系统",
  description: "企业级 RAG 知识库导入、治理、检索、问答与评估系统"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
