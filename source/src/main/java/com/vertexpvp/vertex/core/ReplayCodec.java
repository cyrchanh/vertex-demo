package com.vertexpvp.vertex.core;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.zip.GZIPInputStream;
import java.util.zip.GZIPOutputStream;

/** Gzipped JSON encoding of a {@link ReplayDocument}; the on-disk {@code .json.gz} replay format. */
public final class ReplayCodec {

	private static final Gson GSON = new GsonBuilder().serializeSpecialFloatingPointValues().create();

	private ReplayCodec() {
	}

	public static byte[] encode(ReplayDocument doc) throws IOException {
		ByteArrayOutputStream bytes = new ByteArrayOutputStream();
		try (Writer w = new OutputStreamWriter(new GZIPOutputStream(bytes), StandardCharsets.UTF_8)) {
			GSON.toJson(doc, w);
		}
		return bytes.toByteArray();
	}

	public static ReplayDocument decode(byte[] data) throws IOException {
		try (Reader r = new InputStreamReader(new GZIPInputStream(new ByteArrayInputStream(data)), StandardCharsets.UTF_8)) {
			ReplayDocument doc = GSON.fromJson(r, ReplayDocument.class);
			if (doc == null || doc.trace() == null) {
				throw new IOException("not a Vertex replay");
			}
			return doc;
		}
	}
}
