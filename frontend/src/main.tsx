import { createRoot } from "react-dom/client";
import { ConfigProvider, App as AntApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: "#1D5FA7",
        colorBgLayout: "#F4F7FB",
        colorText: "#172033",
        colorTextSecondary: "#526070",
        colorBorder: "#DCE3EC",
        borderRadius: 6,
        fontSize: 14,
        fontFamily:
          '-apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',
      },
      components: {
        Layout: {
          headerBg: "#ffffff",
          siderBg: "#ffffff",
          bodyBg: "#F4F7FB",
        },
        Menu: {
          itemHeight: 42,
          itemSelectedBg: "#EAF2FB",
          itemSelectedColor: "#1D5FA7",
          itemHoverBg: "#F4F7FB",
        },
        Table: {
          cellPaddingBlock: 8,
          headerBg: "#F8FAFC",
          headerColor: "#526070",
          borderColor: "#E9EEF5",
        },
      },
    }}
  >
    <AntApp>
      <App />
    </AntApp>
  </ConfigProvider>,
);
