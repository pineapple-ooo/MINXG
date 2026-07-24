// Package export provides C-exported functions for AgentHarness Go core.
// Build: go build -buildmode=c-shared -o ../../libagent_harness_go.so .
package main

/*
#cgo LDFLAGS: -L/storage/emulated/0/AgentHarness-Beta-0.11.0 -lagent_harness_c -lagent_harness_core -lpthread
#cgo CFLAGS: -I/storage/emulated/0/AgentHarness-Beta-0.11.0/c_core -I/storage/emulated/0/AgentHarness-Beta-0.11.0/cpp_core
#include <stdlib.h>
#include <text_engine.h>
#include <agent_harness_arch.h>
#include <mem_pool.h>

// Extra Go-side utilities (pure Go, no C dependency)
#include <string.h>
*/
import "C"
import (
	"runtime"
	"unsafe"
)

// GoVersion returns the Go runtime version string.
func version() string { return runtime.Version() }

//export AgentHarnessGoVersion
func AgentHarnessGoVersion() *C.char {
	return C.CString(version())
}

//export AgentHarnessHealthCheck
func AgentHarnessHealthCheck() C.int {
	return 1
}

//export AgentHarnessTextSearchBMH
// Returns first match offset or -1. Pure C implementation.
func AgentHarnessTextSearchBMH(haystack *C.char, hlen C.size_t, needle *C.char, nlen C.size_t) C.longlong {
	if haystack == nil || needle == nil || nlen == 0 || hlen < nlen {
		return C.longlong(-1)
	}
	pos := C.agent_harness_memmem((*C.uchar)(unsafe.Pointer(haystack)), hlen,
		(*C.uchar)(unsafe.Pointer(needle)), nlen)
	return C.longlong(pos)
}

//export AgentHarnessTextSearchBMHReverse
// Returns last match offset or -1.
func AgentHarnessTextSearchBMHReverse(haystack *C.char, hlen C.size_t, needle *C.char, nlen C.size_t) C.longlong {
	if haystack == nil || needle == nil || nlen == 0 || hlen < nlen {
		return C.longlong(-1)
	}
	pos := C.agent_harness_memrmem((*C.uchar)(unsafe.Pointer(haystack)), hlen,
		(*C.uchar)(unsafe.Pointer(needle)), nlen)
	return C.longlong(pos)
}

//export AgentHarnessTextCount
// Count occurrences of needle in haystack.
func AgentHarnessTextCount(haystack *C.char, hlen C.size_t, needle *C.char, nlen C.size_t) C.int {
	if haystack == nil || needle == nil || nlen == 0 || hlen < nlen {
		return 0
	}
	return C.int(C.agent_harness_memcnt((*C.uchar)(unsafe.Pointer(haystack)), hlen,
		(*C.uchar)(unsafe.Pointer(needle)), nlen))
}

//export AgentHarnessStrLower
// In-place lowercase. Returns new length.
func AgentHarnessStrLower(str *C.char, len C.size_t) C.size_t {
	if str == nil || len == 0 {
		return 0
	}
	return C.agent_harness_str_lower(str, len)
}

//export AgentHarnessStrUpper
// In-place uppercase. Returns new length.
func AgentHarnessStrUpper(str *C.char, len C.size_t) C.size_t {
	if str == nil || len == 0 {
		return 0
	}
	return C.agent_harness_str_upper(str, len)
}

//export AgentHarnessStrTrim
// In-place trim. Returns new length.
func AgentHarnessStrTrim(str *C.char, len C.size_t) C.size_t {
	if str == nil || len == 0 {
		return 0
	}
	return C.agent_harness_str_trim(str, len)
}

//export AgentHarnessGlobMatch
// Returns 1 if pattern matches str, 0 otherwise.
func AgentHarnessGlobMatch(pattern, str *C.char) C.int {
	if pattern == nil || str == nil {
		return 0
	}
	if C.agent_harness_fnmatch(pattern, str) {
		return 1
	}
	return 0
}

//export AgentHarnessGlobMatchCI
// Case-insensitive glob match.
func AgentHarnessGlobMatchCI(pattern, str *C.char) C.int {
	if pattern == nil || str == nil {
		return 0
	}
	if C.agent_harness_fnmatch_caseless(pattern, str) {
		return 1
	}
	return 0
}

//export AgentHarnessUtf8Valid
// Returns 1 if valid UTF-8, 0 otherwise.
func AgentHarnessUtf8Valid(str *C.char, len C.size_t) C.int {
	if str == nil || len == 0 {
		return 1
	}
	if C.agent_harness_utf8_is_valid(str, len) {
		return 1
	}
	return 0
}

//export AgentHarnessUtf8Codepoints
// Returns number of Unicode codepoints.
func AgentHarnessUtf8Codepoints(str *C.char, len C.size_t) C.int {
	if str == nil || len == 0 {
		return 0
	}
	return C.int(C.agent_harness_utf8_codepoint_count(str, len))
}

//export AgentHarnessSlugify
// Slugify input: lowercase, strip non-word, collapse dashes.
// Returns bytes written to out_buf.
func AgentHarnessSlugify(input *C.char, inLen C.size_t, outBuf *C.char, outCap C.size_t) C.size_t {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.agent_harness_slugify(input, inLen, outBuf, outCap)
}

//export AgentHarnessTruncate
// Truncate input to maxLen, append suffix if truncated.
// Returns final length.
func AgentHarnessTruncate(input *C.char, inLen C.size_t, maxLen C.size_t,
	suffix *C.char, sufLen C.size_t,
	outBuf *C.char, outCap C.size_t) C.size_t {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.agent_harness_truncate(input, inLen, maxLen, suffix, sufLen, outBuf, outCap)
}

//export AgentHarnessWordFreqHash
// Word frequency analysis. out_buf gets "word1:N1,word2:N2,..." sorted desc.
// Returns bytes written, 0 on error.
func AgentHarnessWordFreqHash(input *C.char, inLen C.size_t, topN C.int,
	outBuf *C.char, outCap C.size_t) C.size_t {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.agent_harness_word_freq_hash(input, inLen, topN, outBuf, outCap)
}

//export AgentHarnessNormalizeWS
// Normalize whitespace: trim, collapse spaces, unify line endings.
// line_ending: 0='\n', 1='\r\n', 2='\r'.
// Returns final length.
func AgentHarnessNormalizeWS(input *C.char, inLen C.size_t, lineEnding C.int,
	outBuf *C.char, outCap C.size_t) C.size_t {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.agent_harness_normalize_ws(input, inLen, lineEnding, outBuf, outCap)
}

//export AgentHarnessBaseConvert
// Convert number string from base_fr to base_to. Supports 2-36.
// Returns length of result (0 on error).
func AgentHarnessBaseConvert(number *C.char, baseFr C.int, baseTo C.int,
	outBuf *C.char, outCap C.size_t) C.int {
	if number == nil || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.agent_harness_base_convert(number, baseFr, baseTo, outBuf, outCap)
}

//export AgentHarnessExtractURLs
// Extract HTTP/HTTPS URLs from input. Returns count.
// out_buf receives null-separated list.
func AgentHarnessExtractURLs(input *C.char, inLen C.size_t,
	outBuf *C.char, outCap C.size_t, maxUrls C.int) C.int {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.int(C.agent_harness_extract_urls(input, inLen, outBuf, outCap, maxUrls))
}

//export AgentHarnessExtractEmails
// Extract email addresses. Returns count.
func AgentHarnessExtractEmails(input *C.char, inLen C.size_t,
	outBuf *C.char, outCap C.size_t, maxEmails C.int) C.int {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.int(C.agent_harness_extract_emails(input, inLen, outBuf, outCap, maxEmails))
}

//export AgentHarnessExtractHashtags
// Extract #hashtags. Returns count.
func AgentHarnessExtractHashtags(input *C.char, inLen C.size_t,
	outBuf *C.char, outCap C.size_t, maxTags C.int) C.int {
	if input == nil || inLen == 0 || outBuf == nil || outCap == 0 {
		return 0
	}
	return C.int(C.agent_harness_extract_hashtags(input, inLen, outBuf, outCap, maxTags))
}

//export AgentHarnessArenaStats
// Returns arena stats: total, used, blockCount as 3 values.
// Takes arena pointer (from AgentHarnessArenaCreate) as opaque uint64.
func AgentHarnessArenaStats(arenaPtr uint64) (total, used, blocks C.int) {
	// Note: we'd need to reconstruct *C.agent_harness_arena_t from uint64
	// For now return zeros as placeholder (real impl would store map[uint64]*C.agent_harness_arena_t)
	return 0, 0, 0
}

//export AgentHarnessFree
// Free C-allocated memory (for strings returned to Go caller).
func AgentHarnessFree(ptr unsafe.Pointer) {
	C.free(ptr)
}

func main() {} // Required for c-shared
