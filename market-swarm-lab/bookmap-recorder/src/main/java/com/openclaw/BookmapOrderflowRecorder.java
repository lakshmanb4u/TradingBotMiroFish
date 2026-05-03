package com.openclaw;

import java.io.*;
import java.nio.file.*;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicLong;

import velox.api.layer1.Layer1ApiDataAdapter;
import velox.api.layer1.Layer1ApiFinishable;
import velox.api.layer1.Layer1ApiInstrumentAdapter;
import velox.api.layer1.Layer1ApiProvider;
import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1Attachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.common.ListenableHelper;
import velox.api.layer1.data.InstrumentInfo;
import velox.api.layer1.data.TradeInfo;

@Layer1Attachable
@Layer1StrategyName("OrderflowRecorder")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION2)
public class BookmapOrderflowRecorder implements Layer1ApiDataAdapter, Layer1ApiInstrumentAdapter, Layer1ApiFinishable {

    private static final String OUTPUT_DIR = "/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab/state/orderflow/bookmap_api";
    private static final DateTimeFormatter DATE_FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC);

    private final AtomicLong sequence = new AtomicLong(0);
    private BufferedWriter writer;
    private String currentDate;
    private Map<String, InstrumentInfo> instruments = new TreeMap<>();

    public BookmapOrderflowRecorder(Layer1ApiProvider provider) throws IOException {
        Files.createDirectories(Paths.get(OUTPUT_DIR));
        openWriter();
        ListenableHelper.addListeners(provider, this);
    }

    private synchronized void openWriter() throws IOException {
        currentDate = DATE_FMT.format(Instant.now());
        String filename = OUTPUT_DIR + "/es_orderflow_" + currentDate + ".jsonl";
        writer = new BufferedWriter(new FileWriter(filename, true));
    }

    private synchronized void rolloverIfNeeded() throws IOException {
        String today = DATE_FMT.format(Instant.now());
        if (!today.equals(currentDate)) {
            writer.close();
            openWriter();
        }
    }

    private static final DateTimeFormatter TS_FMT = DateTimeFormatter
        .ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
        .withZone(ZoneOffset.UTC);

    private static String formatUtc(Instant instant) {
        return TS_FMT.format(instant);
    }

    private synchronized void writeEvent(String type, String symbol, double price, int size,
            String side, double bidPrice, double askPrice, int bidSize, int askSize,
            int level, String extras, Instant tsEventOpt) throws IOException {
        rolloverIfNeeded();

        long seq = sequence.incrementAndGet();
        Instant now = Instant.now();
        String tsEvent = formatUtc(tsEventOpt != null ? tsEventOpt : now);
        String tsRecv = formatUtc(now);

        StringBuilder sb = new StringBuilder();
        sb.append("{");
        sb.append("\"seq\":" ).append(seq).append(",");
        sb.append("\"ts_event\":\"").append(tsEvent).append("\",");
        sb.append("\"ts_recv\":\"").append(tsRecv).append("\",");
        sb.append("\"symbol\":\"").append(symbol).append("\",");
        sb.append("\"event_type\":\"").append(type).append("\",");
        sb.append("\"price\":").append(price >= 0 ? String.format("%.2f", price) : "null").append(",");
        sb.append("\"size\":").append(size >= 0 ? size : "null").append(",");
        sb.append("\"side\":\"").append(side).append("\",");
        sb.append("\"bid_price\":").append(bidPrice >= 0 ? String.format("%.2f", bidPrice) : "null").append(",");
        sb.append("\"ask_price\":").append(askPrice >= 0 ? String.format("%.2f", askPrice) : "null").append(",");
        sb.append("\"bid_size\":").append(bidSize >= 0 ? bidSize : "null").append(",");
        sb.append("\"ask_size\":").append(askSize >= 0 ? askSize : "null").append(",");
        sb.append("\"level\":").append(level >= 0 ? level : "null").append(",");
        sb.append("\"source\":\"bookmap_l1_api\"");
        if (extras != null && !extras.isEmpty()) {
            sb.append(",").append(extras);
        }
        sb.append("}");

        writer.write(sb.toString());
        writer.newLine();
        writer.flush();
    }

    @Override
    public void onInstrumentAdded(String alias, InstrumentInfo instrumentInfo) {
        instruments.put(alias, instrumentInfo);
        try {
            writeEvent("instrument_added", alias, -1, -1, "unknown", -1, -1, -1, -1, -1,
                "\"pips\":" + instrumentInfo.pips + ",\"exchange\":\"" + instrumentInfo.exchange + "\"", null);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public void onInstrumentRemoved(String alias) {
        instruments.remove(alias);
    }

    @Override
    public void onDepth(String alias, boolean isBid, int price, int size) {
        InstrumentInfo info = instruments.get(alias);
        if (info == null) return;

        double realPrice = info.pips * price;
        String side = isBid ? "bid" : "ask";

        try {
            writeEvent("depth", alias, realPrice, size, side, -1, -1, -1, -1, -1, null, null);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public void onTrade(String alias, double price, int size, TradeInfo tradeInfo) {
        InstrumentInfo info = instruments.get(alias);
        if (info == null) return;

        double realPrice = info.pips * price;
        String aggressor = tradeInfo.isBidAggressor ? "sell" : "buy";

        try {
            writeEvent("trade", alias, realPrice, size, aggressor, -1, -1, -1, -1, -1,
                "\"is_bid_aggressor\":" + tradeInfo.isBidAggressor, null);
        } catch (IOException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public void finish() {
        try {
            if (writer != null) {
                writer.close();
            }
        } catch (IOException e) {
            // ignore on shutdown
        }
    }
}
