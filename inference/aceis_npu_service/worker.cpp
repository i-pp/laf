/*
 * KISS Open License Version 1.0 (KOL-v1)
 *
 * SPDX-FileCopyrightText: 2026 I-plusplus Inc.
 * SPDX-License-Identifier: LicenseRef-KOL-v1
 *
 * Copyright remains with its respective holders; original copyright and attribution notices must be preserved.
 * Contributors may add copyright notices for their own original modifications.
 * Distributed modifications must be identified as required by Section 4(c) of KOL-v1.
 *
 * Use, modification, distribution, and incorporation into commercial or proprietary software are permitted
 * without license fees or source disclosure, subject to all terms of the complete KOL-v1 license.
 *
 * Contributors grant the patent permissions stated in KOL-v1. Outputs are not covered merely because a
 * KOL-licensed tool produced them; embedded licensed material remains subject to applicable license terms.
 *
 * THIS SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OR LIABILITY TO THE EXTENT PERMITTED BY APPLICABLE LAW.
 *
 * This header is a summary, not a separate license or an amendment to KOL-v1.
 * All terms and conditions of KOL-v1 apply. See the accompanying LICENSE file for the complete license text.
 */

// Persistent, pinned-shape ORT C API worker. Vendor binaries/models are not shipped.
#include "onnxruntime_c_api.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

struct Runtime {
  const OrtApi* api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
  OrtEnv* env = nullptr;
  OrtSessionOptions* opts = nullptr;
  OrtSession* session = nullptr;
  OrtMemoryInfo* mem = nullptr;
  OrtAllocator* alloc = nullptr;
  OrtValue* input = nullptr;
  char* output_name = nullptr;
  std::vector<float> values = std::vector<float>(3072);
  void check(OrtStatus* s) { if (s) { std::string m=api->GetErrorMessage(s); api->ReleaseStatus(s); throw std::runtime_error(m); } }
  ~Runtime() {
    if (output_name && alloc) alloc->Free(alloc, output_name);
    if (input) api->ReleaseValue(input);
    if (mem) api->ReleaseMemoryInfo(mem);
    if (session) api->ReleaseSession(session);
    if (opts) api->ReleaseSessionOptions(opts);
    if (env) api->ReleaseEnv(env);
  }
  std::vector<float> run() {
    const char* names[]={"input"}; const OrtValue* inputs[]={input};
    const char* outputs[]={output_name}; OrtValue* out=nullptr;
    check(api->Run(session,nullptr,names,inputs,1,outputs,1,&out));
    try {
      OrtTensorTypeAndShapeInfo* info=nullptr; check(api->GetTensorTypeAndShape(out,&info));
      ONNXTensorElementDataType type; size_t rank=0, count=0; int64_t dims[2]={};
      try {
        check(api->GetTensorElementType(info,&type)); check(api->GetDimensionsCount(info,&rank));
        check(api->GetTensorShapeElementCount(info,&count));
        if (rank!=2 || count!=10 || type!=ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) throw std::runtime_error("unexpected output type/shape");
        check(api->GetDimensions(info,dims,2));
        if (dims[0]!=1 || dims[1]!=10) throw std::runtime_error("unexpected output dimensions");
      } catch (...) { api->ReleaseTensorTypeAndShapeInfo(info); throw; }
      api->ReleaseTensorTypeAndShapeInfo(info);
      float* data=nullptr; check(api->GetTensorMutableData(out,reinterpret_cast<void**>(&data)));
      std::vector<float> result(data,data+10);
      for (float v:result) if (!std::isfinite(v)) throw std::runtime_error("nonfinite output");
      api->ReleaseValue(out); return result;
    } catch (...) { api->ReleaseValue(out); throw; }
  }
};
static void parse_input(const std::string& line, std::vector<float>& values) {
      if (line.size()>100000) throw std::runtime_error("request too large");
      std::istringstream in(line);
      for (float& v:values) {
        double parsed;
        if (!(in>>parsed) || !std::isfinite(parsed)) throw std::runtime_error("invalid input");
        v=static_cast<float>(parsed);
        if (!std::isfinite(v)) throw std::runtime_error("input outside float32 range");
      }
      std::string extra; if (in>>extra) throw std::runtime_error("extra input");
}
int main(int argc,char** argv) {
  try {
    if (argc!=4) throw std::runtime_error("usage: worker MODEL CPU|VitisAI PROFILE_PREFIX");
    const std::string provider=argv[2];
    if (provider!="CPU" && provider!="VitisAI") throw std::runtime_error("invalid provider");
    Runtime r; if (!r.api) throw std::runtime_error("ORT API version mismatch");
    r.check(r.api->CreateEnv(ORT_LOGGING_LEVEL_WARNING,"aceis-npu",&r.env));
    r.check(r.api->CreateSessionOptions(&r.opts));
    r.check(r.api->SetIntraOpNumThreads(r.opts,1)); r.check(r.api->SetInterOpNumThreads(r.opts,1));
    if (provider=="VitisAI") {
      r.check(r.api->AddSessionConfigEntry(r.opts,"session.disable_cpu_ep_fallback","1"));
      r.check(r.api->SessionOptionsAppendExecutionProvider(r.opts,"VitisAI",nullptr,nullptr,0));
    }
    r.check(r.api->EnableProfiling(r.opts,argv[3]));
    r.check(r.api->CreateSession(r.env,argv[1],r.opts,&r.session));
    r.check(r.api->GetAllocatorWithDefaultOptions(&r.alloc));
    size_t outputs=0; r.check(r.api->SessionGetOutputCount(r.session,&outputs));
    if (outputs!=1) throw std::runtime_error("expected exactly one output");
    r.check(r.api->SessionGetOutputName(r.session,0,r.alloc,&r.output_name));
    r.check(r.api->CreateCpuMemoryInfo(OrtArenaAllocator,OrtMemTypeDefault,&r.mem));
    const int64_t dims[]={1,3,32,32};
    r.check(r.api->CreateTensorWithDataAsOrtValue(r.mem,r.values.data(),r.values.size()*sizeof(float),dims,4,ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,&r.input));
    for(size_t i=0;i<r.values.size();++i) r.values[i]=static_cast<float>(i%251)/250.0f;
    r.run(); // One bounded warmup, captured in a fresh ORT execution profile.
    char* profile=nullptr; r.check(r.api->SessionEndProfiling(r.session,r.alloc,&profile));
    // PROFILE_PREFIX is operator-controlled; reject JSON control/escape characters.
    std::string path(profile); r.alloc->Free(r.alloc,profile);
    if (path.find_first_of("\"\\\n\r")!=std::string::npos) throw std::runtime_error("unsafe profile path");
    std::cout<<"ACEIS_JSON {\"ready\":true,\"profile\":\""<<path<<"\",\"ort_version\":\""<<OrtGetApiBase()->GetVersionString()<<"\"}"<<std::endl;
    std::string line;
    while (std::getline(std::cin,line)) {
      parse_input(line,r.values);
      const auto begin=std::chrono::steady_clock::now(); auto out=r.run();
      double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count();
      std::cout<<std::setprecision(9)<<"ACEIS_JSON {\"logits\":[";
      for(size_t i=0;i<out.size();++i) std::cout<<(i?",":"")<<out[i];
      std::cout<<"],\"class_index\":"<<std::distance(out.begin(),std::max_element(out.begin(),out.end()))<<",\"latency_ms\":"<<ms<<"}"<<std::endl;
    }
    return 0;
  } catch (const std::exception& e) { std::cerr<<"worker failed: "<<e.what()<<std::endl; return 1; }
}
